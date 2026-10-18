use super::Record;
use rusqlite::{Connection, OptionalExtension, params};
use std::sync::Mutex;

// One embedded SQLite connection, KV JSON values and PDF bytes, entirely in RAM.
pub struct Store(Mutex<Connection>);
impl Store {
    pub fn new() -> anyhow::Result<Self> {
        let connection = Connection::open_in_memory()?;
        connection.execute_batch(
            "CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, pdf BLOB NOT NULL);",
        )?;
        Ok(Self(Mutex::new(connection)))
    }
    fn lock(&self) -> anyhow::Result<std::sync::MutexGuard<'_, Connection>> {
        self.0
            .lock()
            .map_err(|_| anyhow::anyhow!("Session store unavailable"))
    }
    pub fn usage(&self) -> anyhow::Result<(usize, usize)> {
        Ok(self.lock()?.query_row(
            "SELECT count(*), coalesce(sum(length(pdf)),0) FROM kv",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?)
    }
    pub fn insert(&self, record: &Record, pdf: &[u8]) -> anyhow::Result<()> {
        let db = self.lock()?;
        let (count, bytes): (u64, u64) = db.query_row(
            "SELECT count(*), coalesce(sum(length(pdf)),0) FROM kv",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        anyhow::ensure!(
            count < 100 && bytes + pdf.len() as u64 <= 200 * 1024 * 1024,
            "Session is full (100 invoices / 200 MB). Export and clear the queue."
        );
        db.execute(
            "INSERT INTO kv VALUES (?1,?2,?3)",
            params![record.id, serde_json::to_string(record)?, pdf],
        )?;
        Ok(())
    }
    pub fn get(&self, id: &str) -> anyhow::Result<Option<Record>> {
        let value: Option<String> = self
            .lock()?
            .query_row("SELECT value FROM kv WHERE key=?1", [id], |r| r.get(0))
            .optional()?;
        value
            .map(|s| serde_json::from_str(&s).map_err(Into::into))
            .transpose()
    }
    pub fn pdf(&self, id: &str) -> anyhow::Result<Option<Vec<u8>>> {
        Ok(self
            .lock()?
            .query_row("SELECT pdf FROM kv WHERE key=?1", [id], |r| r.get(0))
            .optional()?)
    }
    pub fn list(&self) -> anyhow::Result<Vec<Record>> {
        let db = self.lock()?;
        let mut statement = db.prepare("SELECT value FROM kv ORDER BY rowid")?;
        let values = statement
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        values
            .into_iter()
            .map(|s| serde_json::from_str(&s).map_err(Into::into))
            .collect()
    }
    pub fn update(&self, record: &Record, expected: u64) -> anyhow::Result<bool> {
        Ok(self.lock()?.execute(
            "UPDATE kv SET value=?1 WHERE key=?2 AND json_extract(value,'$.revision')=?3",
            params![serde_json::to_string(record)?, record.id, expected],
        )? == 1)
    }
    pub fn clear(&self) -> anyhow::Result<()> {
        self.lock()?.execute("DELETE FROM kv", [])?;
        Ok(())
    }
    pub fn delete(&self, id: &str) -> anyhow::Result<bool> {
        Ok(self.lock()?.execute("DELETE FROM kv WHERE key=?1", [id])? == 1)
    }
}
