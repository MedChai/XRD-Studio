//! XRD Studio Tauri Shell
//!
//! 职责(README §3/§11.2):
//!  - 监管 Python 分析引擎 sidecar(stdio + 长度前缀帧,退避重启)
//!  - engine_command:前端唯一引擎入口,请求按 id 路由
//!  - Data Store 路径解析(默认 %LOCALAPPDATA%\XRDStudio,§11.4)

use std::collections::HashMap;
use std::io::{Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Sender};
use std::sync::{Mutex, OnceLock};
use std::thread;

use serde_json::Value;

/// 一个请求的响应通道:{"id": n, ...engine 响应}
type ResponseMap = HashMap<u64, Sender<Value>>;

struct Engine {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<Stdin>>,
    pending: Mutex<ResponseMap>,
    next_id: AtomicU64,
}

/// stdin 需要 WriteExt + 发送安全
type Stdin = Box<dyn Write + Send>;

static ENGINE: OnceLock<Engine> = OnceLock::new();

fn engine() -> &'static Engine {
    ENGINE.get_or_init(|| Engine {
        child: Mutex::new(None),
        stdin: Mutex::new(None),
        pending: Mutex::new(HashMap::new()),
        next_id: AtomicU64::new(1),
    })
}

// ------------------------------------------------------------------
// sidecar 定位:开发用 engine/.venv,生产用同目录 xrd-engine.exe(§11.1)
// ------------------------------------------------------------------

fn sidecar_command(data_root: &std::path::Path) -> Command {
    // XRD_ENGINE 环境变量可强制指定(打包决策实验用,§11.1)
    if let Ok(exe) = std::env::var("XRD_ENGINE") {
        let mut c = Command::new(exe);
        c.env("XRD_DATA_ROOT", data_root);
        return c;
    }

    if cfg!(debug_assertions) {
        // dev:app/../engine/.venv/Scripts/python.exe -m xrd_engine
        let venv_python = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|app| app.parent())
            .map(|root| {
                root.join("engine")
                    .join(".venv")
                    .join("Scripts")
                    .join("python.exe")
            });
        if let Some(py) = venv_python {
            if py.exists() {
                // engine 根目录 = .../engine/.venv/Scripts/python.exe 上溯三级
                let engine_dir = py
                    .parent() // Scripts
                    .and_then(|p| p.parent()) // .venv
                    .and_then(|p| p.parent()) // engine
                    .map(|p| p.to_path_buf());
                let mut c = Command::new(&py);
                c.args(["-m", "xrd_engine"]);
                if let Some(dir) = engine_dir {
                    c.current_dir(dir);
                }
                c.env("XRD_DATA_ROOT", data_root);
                hide_console(&mut c);
                return c;
            }
        }
    }

    // 生产:与主程序同目录的 xrd-engine.exe(sidecar,§11.1 打包产物)
    let exe = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("xrd-engine.exe")))
        .unwrap_or_else(|| std::path::PathBuf::from("xrd-engine.exe"));
    let mut c = Command::new(exe);
    c.env("XRD_DATA_ROOT", data_root);
    hide_console(&mut c);
    c
}

#[cfg(windows)]
fn hide_console(c: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    c.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_c: &mut Command) {}

// ------------------------------------------------------------------
// 帧协议:[u32 BE 长度][UTF-8 JSON](§11.2)
// ------------------------------------------------------------------

fn write_frame(w: &mut dyn Write, obj: &Value) -> std::io::Result<()> {
    let data = serde_json::to_vec(obj)?;
    w.write_all(&(data.len() as u32).to_be_bytes())?;
    w.write_all(&data)?;
    w.flush()
}

fn read_frame(r: &mut dyn Read) -> std::io::Result<Value> {
    let mut header = [0u8; 4];
    r.read_exact(&mut header)?;
    let len = u32::from_be_bytes(header) as usize;
    if len > 64 * 1024 * 1024 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "帧过大",
        ));
    }
    let mut buf = vec![0u8; len];
    r.read_exact(&mut buf)?;
    Ok(serde_json::from_slice(&buf)?)
}

// ------------------------------------------------------------------
// 启动 / 重启 / 读取循环
// ------------------------------------------------------------------

fn ensure_running(data_root: &std::path::Path) -> Result<(), String> {
    let eng = engine();
    let stdin_guard = eng.stdin.lock().unwrap();
    if stdin_guard.is_some() {
        return Ok(()); // 已在运行(简单判定:stdin 句柄存在)
    }
    drop(stdin_guard);

    let mut child = sidecar_command(data_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("无法启动分析引擎:{e}"))?;

    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "引擎无 stdin".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "引擎无 stdout".to_string())?;
    let stderr = child.stderr.take();

    // stderr → 日志文件(logs/engine.log)
    if let Some(mut se) = stderr {
        let log_dir = data_root.join("logs");
        thread::spawn(move || {
            let _ = std::fs::create_dir_all(&log_dir);
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(log_dir.join("engine.log"))
            {
                let mut buf = [0u8; 4096];
                loop {
                    match se.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            let _ = f.write_all(&buf[..n]);
                            let _ = f.flush();
                        }
                    }
                }
            }
        });
    }

    // reader 线程:响应按 id 派发;EOF 时清 pending 并标记进程退出
    let (tx, rx) = mpsc::channel::<()>(); // ready 信标(引擎已 ready 前请求会排队在 OS pipe,这里不阻塞)
    thread::spawn(move || {
        let mut stdout = stdout;
        let _ = rx.recv();
        loop {
            match read_frame(&mut stdout) {
                Ok(resp) => {
                    let id = resp.get("id").and_then(Value::as_u64).unwrap_or(0);
                    let sender = {
                        let mut pending = eng_pending();
                        pending.remove(&id)
                    };
                    if let Some(s) = sender {
                        let _ = s.send(resp);
                    }
                }
                Err(_) => {
                    // 引擎退出:失败所有 pending,释放 stdin 使下次 ensure_running 重启
                    let leftover: Vec<Sender<Value>> = {
                        let mut p = eng_pending();
                        p.drain().map(|(_, s)| s).collect()
                    };
                    for s in leftover {
                        let _ = s.send(serde_json::json!({
                            "ok": false,
                            "error": "分析引擎意外退出,请重试上次操作",
                            "detail": "sidecar EOF",
                        }));
                    }
                    let mut si = engine().stdin.lock().unwrap();
                    *si = None;
                    let mut ch = engine().child.lock().unwrap();
                    if let Some(mut c) = ch.take() {
                        let _ = c.kill();
                    }
                    break;
                }
            }
        }
    });
    let _ = tx.send(());

    let mut si = eng.stdin.lock().unwrap();
    *si = Some(Box::new(stdin) as Stdin);
    let mut ch = eng.child.lock().unwrap();
    *ch = Some(child);
    Ok(())
}

fn eng_pending() -> std::sync::MutexGuard<'static, ResponseMap> {
    engine().pending.lock().unwrap()
}

// ------------------------------------------------------------------
// Tauri command
// ------------------------------------------------------------------

#[tauri::command]
async fn engine_command(cmd: String, payload: String) -> Result<Value, String> {
    let data_root = {
        // %LOCALAPPDATA%\XRDStudio(§11.4:避免 Program Files 权限与非 ASCII 用户目录问题)
        let base = std::env::var("LOCALAPPDATA")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| {
                dirs_fallback()
            });
        let root = base.join("XRDStudio");
        let _ = std::fs::create_dir_all(&root);
        root
    };

    ensure_running(&data_root)?;

    let id = engine().next_id.fetch_add(1, Ordering::SeqCst);
    let payload: Value =
        serde_json::from_str(&payload).unwrap_or(Value::Object(Default::default()));
    let request = serde_json::json!({ "id": id, "cmd": cmd, "payload": payload });

    let (tx, rx) = mpsc::channel();
    eng_pending().insert(id, tx);

    {
        let mut si = engine().stdin.lock().unwrap();
        let writer = si
            .as_mut()
            .ok_or_else(|| "分析引擎未运行".to_string())?;
        write_frame(writer, &request).map_err(|e| format!("发送请求失败:{e}"))?;
    }

    let timeout = std::time::Duration::from_secs(120);
    let resp = rx
        .recv_timeout(timeout)
        .map_err(|_| "分析引擎无响应(超时)".to_string())?;

    // 摘除本次注册(reader 线程已取出,这里只防御竞态残留)
    eng_pending().remove(&id);
    Ok(resp)
}

#[cfg(windows)]
fn dirs_fallback() -> std::path::PathBuf {
    std::env::var("USERPROFILE")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from("."))
}

#[cfg(not(windows))]
fn dirs_fallback() -> std::path::PathBuf {
    std::env::var("HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from("."))
}

// ------------------------------------------------------------------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![engine_command])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
