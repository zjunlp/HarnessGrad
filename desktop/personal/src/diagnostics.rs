use std::{
    fs::OpenOptions,
    io::Write,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Clone)]
pub struct Diagnostics {
    path: PathBuf,
    writer: Arc<Mutex<()>>,
}

impl Diagnostics {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            writer: Arc::new(Mutex::new(())),
        }
    }

    pub fn record(&self, kind: &str, payload: serde_json::Value) {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        let row = format!(
            "{}\n",
            serde_json::json!({"type":kind,"timestamp_ms":timestamp,"payload":payload})
        );
        let Ok(_guard) = self.writer.lock() else {
            return;
        };
        if self
            .path
            .metadata()
            .map(|v| v.len())
            .unwrap_or(0)
            .saturating_add(row.len() as u64)
            > 262144
        {
            return;
        }
        let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        else {
            return;
        };
        let _ = file.write_all(row.as_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn structured_log_refuses_a_row_that_exceeds_the_byte_budget() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("hg-a5-log-{}-{nonce}.jsonl", std::process::id()));
        let log = Diagnostics::new(path.clone());
        log.record("window.ready", serde_json::json!({"visible":true}));
        let before = std::fs::read(&path).unwrap();
        log.record("too_large", serde_json::json!({"body":"x".repeat(262144)}));
        assert_eq!(std::fs::read(&path).unwrap(), before);
        let row: serde_json::Value = serde_json::from_slice(&before).unwrap();
        assert_eq!(row["type"], "window.ready");
        assert_eq!(row["payload"]["visible"], true);
        assert!(before.len() <= 262144);
    }
}
