import { ApiError, API_PREFIX, post, request, type Attachment } from "./client";
import { translate } from "../i18n";
import type { components } from "./schema";

type UploadOut = components["schemas"]["UploadOut"];

async function sha256(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|;\s*)pikirler_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function putBytes(url: string, file: Blob, onProgress: (p: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-CSRF-Token", csrf());
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      let code = "upload_failed", message = "Upload failed";
      try {
        const body = JSON.parse(xhr.responseText);
        if (body?.error) ({ code, message } = body.error);
      } catch {
        /* keep defaults */
      }
      reject(new ApiError(xhr.status, code, message));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", translate("e_upload_net")));
    xhr.send(file);
  });
}

/** Declare → PUT bytes → complete. The server re-checks size, SHA-256 and the
 *  real file type, so a file that lies about itself is rejected. Re-declaring
 *  the same bytes returns the existing upload, so retries are safe. */
export async function uploadFile(file: File, onProgress: (p: number) => void): Promise<Attachment> {
  onProgress(0);
  const checksum = await sha256(file);
  const ticket = await post<UploadOut>("/media/uploads", {
    filename: file.name || "file",
    mime_type: file.type || "application/octet-stream",
    size: file.size,
    checksum_sha256: checksum,
  });
  if (ticket.status !== "pending_upload") {
    onProgress(1);
    return ticket.attachment;
  }
  const url = ticket.upload_url.startsWith(API_PREFIX) ? ticket.upload_url : API_PREFIX + ticket.upload_url;
  await putBytes(url, file, (p) => onProgress(p * 0.95));
  try {
    const done = await request<Attachment>("POST", `/media/uploads/${ticket.id}/complete`);
    onProgress(1);
    return done;
  } catch (e) {
    if (e instanceof ApiError && e.code === "upload_completed") {
      onProgress(1);
      return ticket.attachment;
    }
    throw e;
  }
}
