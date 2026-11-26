//! XRD Studio Tauri Shell
//!
//! 职责(README §3/§11.2):
//!  - 监管 Python 分析引擎 sidecar(stdio + 长度前缀帧,退避重启)
//!  - engine_command:前端唯一引擎入口,请求按 id 路由
//!  - 在软件目录维护项目库，并向引擎提供旧版库的只读迁移来源

use std::collections::HashMap;
use std::io::{Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Sender};
use std::sync::{Mutex, OnceLock};
use std::thread;

use serde_json::Value;

/// 一个请求的响应通道:{"id": n, ...engine 响应}
/// 一个响应 = JSON 头 + 可选二进制体(曲线信封,§9.1)。
/// 二进制体不进 JSON —— 那正是这个改动的目的。
type Frame = (Value, Vec<u8>);
type ResponseMap = HashMap<u64, Sender<Frame>>;

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

/// sidecar 启动互斥锁:确保任意时刻只有一个线程在 spawn。
static START_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn start_lock() -> &'static Mutex<()> {
    START_LOCK.get_or_init(|| Mutex::new(()))
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

/// 递归查找响应里的 `__bin__.bytes`。
///
/// 不能只查 `data.__bin__` —— 信封可能嵌在更深一层(见 read_frame 的说明)。
fn find_bin_len(node: &Value) -> Option<usize> {
    match node {
        Value::Object(map) => {
            if let Some(meta) = map.get("__bin__") {
                if let Some(n) = meta.get("bytes").and_then(Value::as_u64) {
                    if n > 0 {
                        return Some(n as usize);
                    }
                }
            }
            map.values().find_map(find_bin_len)
        }
        Value::Array(items) => items.iter().find_map(find_bin_len),
        _ => None,
    }
}

fn read_frame(r: &mut dyn Read) -> std::io::Result<(Value, Vec<u8>)> {
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
    let value: Value = serde_json::from_slice(&buf)?;

    // 曲线信封(§9.1):JSON 头之后可以跟一段二进制体,长度写在
    // __bin__.bytes 里。**长度前缀只覆盖 JSON 头** —— 所以旧读取方
    // (只读头)依然能正确解析所有无二进制的帧,这是刻意的向后兼容。
    //
    // **必须递归找 __bin__。** `datasets.curve` 把它放在 data 顶层,而
    // `analysis.preview` 放在 data.curve 里。只查顶层的话,preview 的
    // 二进制体会被当成"没有二进制"留在管道里,污染**后续每一帧** ——
    // 表现是切到某个数据集后整个软件开始乱套,而错误信息完全没有指向性。
    let bin_len = find_bin_len(&value).unwrap_or(0);
    let mut body = Vec::new();
    if bin_len > 0 {
        if bin_len > 256 * 1024 * 1024 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "二进制体过大",
            ));
        }
        body = vec![0u8; bin_len];
        r.read_exact(&mut body)?;
    }
    Ok((value, body))
}

// ------------------------------------------------------------------
// 启动 / 重启 / 读取循环
// ------------------------------------------------------------------

fn ensure_running(data_root: &std::path::Path, log_dir: &std::path::Path) -> Result<(), String> {
    let eng = engine();
    // 启动全程持锁:并发请求(如首屏 Promise.all)不能都穿过
    // "检查未运行 → spawn" 的间隙,否则 sidecar 被重复拉起。
    let _start_guard = start_lock().lock().unwrap();
    if eng.stdin.lock().unwrap().is_some() {
        return Ok(()); // 已在运行(判定:stdin 句柄存在)
    }

    std::fs::create_dir_all(log_dir)
        .map_err(|e| format!("无法写入软件目录 {}：{e}", log_dir.display()))?;
    let mut command = sidecar_command(data_root);
    command.env("XRD_LOG_DIR", log_dir);
    if std::env::var_os("XRD_DATA_ROOT").is_none() && std::env::var_os("XRD_LEGACY_DATA_ROOT").is_none() {
        if let Some(base) = std::env::var_os("LOCALAPPDATA") {
            command.env("XRD_LEGACY_DATA_ROOT", std::path::PathBuf::from(base).join("XRDStudio"));
        }
    }
    let mut child = command
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
        let log_dir = log_dir.to_path_buf();
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
                Ok((resp, body)) => {
                    let id = resp.get("id").and_then(Value::as_u64).unwrap_or(0);
                    let sender = {
                        let mut pending = eng_pending();
                        pending.remove(&id)
                    };
                    if let Some(s) = sender {
                        let _ = s.send((resp, body));
                    }
                }
                Err(_) => {
                    // 引擎退出:失败所有 pending,释放 stdin 使下次 ensure_running 重启
                    let leftover: Vec<Sender<Frame>> = {
                        let mut p = eng_pending();
                        p.drain().map(|(_, s)| s).collect()
                    };
                    for s in leftover {
                        let _ = s.send((
                            serde_json::json!({
                                "ok": false,
                                "error": "分析引擎意外退出,请重试上次操作",
                                "detail": "sidecar EOF",
                            }),
                            Vec::new(),
                        ));
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

/// 响应打包约定(前端 engine.ts 按同一约定拆):
///
///     [u32 BE jsonLen][json header][binary body]
///
/// 与引擎→Rust 的帧格式一致,前端只需实现一次拆包。用 ipc::Response
/// 而不是 Value 是为了让二进制体走 Tauri 的原始字节通道 —— 若返回
/// Value,serde 会把它转成 JSON 数组(每个字节一个数字),比原来的
/// number[] 还慢。
fn pack_response(value: &Value, body: &[u8]) -> tauri::ipc::Response {
    let json = serde_json::to_vec(value).unwrap_or_else(|_| b"{}".to_vec());
    let mut out = Vec::with_capacity(4 + json.len() + body.len());
    out.extend_from_slice(&(json.len() as u32).to_be_bytes());
    out.extend_from_slice(&json);
    out.extend_from_slice(body);
    tauri::ipc::Response::new(out)
}

#[tauri::command]
async fn engine_command(cmd: String, payload: String) -> Result<tauri::ipc::Response, String> {
    let exe = std::env::current_exe().map_err(|e| format!("无法定位软件目录：{e}"))?;
    let dir = exe.parent().ok_or("无法定位软件目录")?;
    // A binary launched directly from this checkout must use the same library as start.bat.
    let software = dir.ancestors().nth(4).filter(|root| {
        (dir == root.join("app/src-tauri/target/release") || dir == root.join("app/src-tauri/target/debug"))
            && root.join("start.bat").is_file()
    }).unwrap_or(dir);
    let base = software.join("XRDStudio");
    let data_root = std::env::var_os("XRD_DATA_ROOT")
        .map(std::path::PathBuf::from).unwrap_or_else(|| base.join("library"));
    let log_dir = std::env::var_os("XRD_LOG_DIR")
        .map(std::path::PathBuf::from).unwrap_or_else(|| base.join("logs"));
    ensure_running(&data_root, &log_dir)?;

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
    let (resp, body) = rx
        .recv_timeout(timeout)
        .map_err(|_| "分析引擎无响应(超时)".to_string())?;

    // 摘除本次注册(reader 线程已取出,这里只防御竞态残留)
    eng_pending().remove(&id);
    Ok(pack_response(&resp, &body))
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
