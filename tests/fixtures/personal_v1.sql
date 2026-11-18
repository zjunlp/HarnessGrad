-- Frozen stage-A schema, before B1 model budgets.
CREATE TABLE projects (id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE);
CREATE TABLE sessions (project_id TEXT NOT NULL REFERENCES projects(id), source_instance TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(project_id,source_instance,id));
CREATE TABLE source_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, source_instance TEXT NOT NULL, session_id TEXT NOT NULL, event_id TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL, UNIQUE(project_id,source_instance,event_id), FOREIGN KEY(project_id,source_instance,session_id) REFERENCES sessions(project_id,source_instance,id));
CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL CHECK(kind IN ('import','demo')), status TEXT NOT NULL CHECK(status IN ('completed','fixture')), input_sha256 TEXT NOT NULL);
CREATE TABLE evolution_events (job_id TEXT NOT NULL REFERENCES jobs(id), seq INTEGER NOT NULL CHECK(seq>0), type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,seq));
CREATE TABLE questions (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), prompt TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','answered')), response TEXT);
PRAGMA user_version=1;
