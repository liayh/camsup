/** Types des réponses de l'API CamSup (alignés sur app/main.py). */

export type Role = "integrator" | "pharmacist";
export interface Me { id: number; username: string; role: Role; site_ids: number[] | null }

export interface Site { id: number; name: string; notes: string; cameras: number }

export interface Camera {
  id: number; site_id: number; name: string; brand: "hikvision" | "dahua"; host: string; port_http: number;
  username: string; channel: number; enabled: number;
  online: number | null; last_check: number | null; error: string | null;
}

export interface Disk {
  name: string; model: string; serial?: string; state: string; level: "ok" | "warn" | "bad"; ok?: boolean;
  total?: number; used?: number; temp: number | null; hours: number | null; issues: string[];
  smart?: { id: number; name: string; current: number; worst: number; threshold: number; raw: number }[];
  /** Secteurs réalloués (5), en attente (197), illisibles (198), erreurs CRC (199) ; `base` = premier relevé du disque. */
  counters?: { id: number; label: string; raw: number; base: number | null; since: number | null }[];
}
export interface RecorderHealth { brand: string; host: string; site_id: number; last_check: number; disks: Disk[]; error: string }

export interface NvrCamera { id: number; name: string; channel: number; state: string; last_record: string | null }
export interface NvrWatch { host: string; site_id: number; site: string; checked: number; error: string; nvr_time: string; cameras: NvrCamera[] }

export interface Alert { id: number; key: string; camera_id: number | null; kind: string; message: string; opened_ts: number; closed_ts: number | null }

export interface Overview {
  me: Me; sites: Site[]; cameras: Camera[]; alerts: Alert[];
  recorders: RecorderHealth[]; nvr: NvrWatch[]; email: boolean; export_max_minutes: number; now: number;
}

export interface CameraTest { ok: boolean; error?: string; nvr_time?: string; state?: string; last_record?: string | null; disks?: number }

export interface UserRow { id: number; username: string; role: Role; active: number; created_ts: number; last_login_ts: number | null; site_ids: number[] }

export interface ExportJob {
  id: number; site_id: number; site: string; username: string; cameras: string; start_ts: number; end_ts: number;
  reason: string; state: "attente" | "en_cours" | "pret" | "erreur"; progress: string; error: string;
  size: number | null; sha256: string; created_ts: number; done_ts: number | null;
}

export interface Preview { id: string; camera_id: number; start_ts: number; end_ts: number; mode: "copie" | "conversion"; state: "attente" | "en_cours" | "pret" | "erreur"; error: string }

export type EmailSecurity = "starttls" | "ssl" | "aucune";
export interface EmailSettings {
  host: string; port: number; security: EmailSecurity; user: string; has_password: boolean; sender: string; to: string[];
  source: string; configured: boolean; last: { ts: number; ok: boolean; error: string } | null;
}

export interface NvrChannelFound { channel: number; state: string; title: string; name: string; known: boolean }
export interface NvrFound { model: string; serial: string; channels: NvrChannelFound[] }
