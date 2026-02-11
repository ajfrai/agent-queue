"""Plan review server. Run: uv run python serve.py"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI()

DATA_DIR = Path(__file__).parent / "data"
PLAN_PATH = Path(__file__).parent / "PLAN.md"
COMMENTS_PATH = DATA_DIR / "comments.json"
APPROVALS_PATH = DATA_DIR / "approvals.json"

DATA_DIR.mkdir(exist_ok=True)


# --- Models ---

class CommentIn(BaseModel):
    snippet: str = ""
    body: str
    section: str = ""

class ApprovalIn(BaseModel):
    section: str
    status: str  # approved | denied | pending


# --- Persistence helpers ---

def _read_json(path: Path) -> list | dict:
    if not path.exists():
        return [] if "comments" in path.name else {}
    return json.loads(path.read_text())

def _write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


# --- API ---

@app.get("/api/plan")
def get_plan():
    return PlainTextResponse(PLAN_PATH.read_text())

@app.get("/api/comments")
def get_comments():
    return _read_json(COMMENTS_PATH)

@app.post("/api/comments")
def add_comment(c: CommentIn):
    comments = _read_json(COMMENTS_PATH)
    entry = {
        "id": len(comments) + 1,
        "snippet": c.snippet,
        "body": c.body,
        "section": c.section,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    comments.append(entry)
    _write_json(COMMENTS_PATH, comments)
    return entry

@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int):
    comments = _read_json(COMMENTS_PATH)
    comments = [c for c in comments if c.get("id") != comment_id]
    _write_json(COMMENTS_PATH, comments)
    return {"ok": True}

@app.patch("/api/comments/{comment_id}")
def edit_comment(comment_id: int, c: CommentIn):
    comments = _read_json(COMMENTS_PATH)
    for comment in comments:
        if comment.get("id") == comment_id:
            comment["body"] = c.body
            break
    _write_json(COMMENTS_PATH, comments)
    return {"ok": True}

@app.get("/api/approvals")
def get_approvals():
    return _read_json(APPROVALS_PATH)

@app.post("/api/approvals")
def set_approval(a: ApprovalIn):
    approvals = _read_json(APPROVALS_PATH)
    if not isinstance(approvals, dict):
        approvals = {}
    approvals[a.section] = {"status": a.status, "ts": datetime.utcnow().isoformat() + "Z"}
    _write_json(APPROVALS_PATH, approvals)
    return approvals[a.section]


# --- UI ---

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Plan Review</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
:root {
  --bg: #ffffff;
  --surface: #f5f5f5;
  --surface2: #ebebeb;
  --border: #ddd;
  --text: #1a1a1a;
  --text2: #777;
  --accent: #1a1a1a;
  --approve: #15803d;
  --approve-bg: #dcfce7;
  --deny: #dc2626;
  --deny-bg: #fee2e2;
  --code-bg: #f8f8f8;
  --code-text: #d14;
  --link: #2563eb;
  --selection: rgba(37,99,235,.2);
}
*{margin:0;padding:0;box-sizing:border-box}
html{font-size:15px}
body{background:var(--bg);color:var(--text);font-family:Inter,-apple-system,system-ui,sans-serif;-webkit-font-smoothing:antialiased}

#app{display:flex;flex-direction:column;height:100dvh}
#plan{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:1.2rem}
#plan-inner{max-width:720px;margin:0 auto}

#plan-inner h1{font-size:1.6rem;font-weight:700;color:var(--text);margin:2rem 0 .8rem;line-height:1.25}
#plan-inner h2{font-size:1.3rem;font-weight:600;color:var(--text);margin:2rem 0 .6rem;padding:.7rem .8rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;line-height:1.3}
#plan-inner h3{font-size:1.1rem;font-weight:600;color:#444;margin:1.4rem 0 .5rem}
#plan-inner p{margin:.6rem 0;line-height:1.7;color:#333}
#plan-inner ul,#plan-inner ol{margin:.5rem 0 .5rem 1.4rem;color:#333}
#plan-inner li{margin:.3rem 0;line-height:1.6}
#plan-inner li::marker{color:var(--text2)}
#plan-inner strong{color:var(--text);font-weight:600}
#plan-inner a{color:var(--link);text-decoration:none}
#plan-inner a:hover{text-decoration:underline}
#plan-inner code{font-family:'JetBrains Mono',Menlo,monospace;font-size:.85em;background:var(--code-bg);border:1px solid var(--border);padding:.15em .35em;border-radius:4px;color:var(--code-text)}
#plan-inner pre{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;padding:1rem;overflow-x:auto;margin:.8rem 0;line-height:1.5}
#plan-inner pre code{background:none;border:none;padding:0;color:#333;font-size:.82rem}
#plan-inner hr{border:none;border-top:1px solid var(--border);margin:1.5rem 0}

.approval-row{display:flex;gap:6px;margin-top:6px}
.approval-row button{
  font-size:.7rem;font-weight:600;letter-spacing:.03em;text-transform:uppercase;
  border:1px solid var(--border);border-radius:4px;padding:3px 10px;cursor:pointer;
  transition:all .15s;color:var(--text2);background:var(--bg);
}
.approval-row button:active{transform:scale(.95)}
.approval-row button.on-approve{background:var(--approve-bg);color:var(--approve);border-color:var(--approve)}
.approval-row button.on-deny{background:var(--deny-bg);color:var(--deny);border-color:var(--deny)}
.approval-row button:hover{border-color:#999}

::selection{background:var(--selection)}

/* FAB */
#fab{
  position:fixed;bottom:20px;right:20px;z-index:100;
  width:56px;height:56px;border-radius:50%;border:none;
  background:var(--accent);color:#fff;font-size:1.4rem;
  cursor:pointer;box-shadow:0 2px 12px rgba(0,0,0,.18);
  transition:transform .15s,box-shadow .15s;
  display:flex;align-items:center;justify-content:center;
}
#fab:active{transform:scale(.92)}
#fab:hover{box-shadow:0 4px 20px rgba(0,0,0,.25)}

#fab-menu{
  position:fixed;bottom:86px;right:20px;z-index:100;
  display:none;flex-direction:column;gap:8px;align-items:flex-end;
}
#fab-menu.open{display:flex}
#fab-menu button{
  padding:10px 20px;font-size:.85rem;font-weight:600;border-radius:8px;
  border:none;cursor:pointer;transition:all .15s;
  box-shadow:0 2px 8px rgba(0,0,0,.12);white-space:nowrap;
}
#fab-menu button:active{transform:scale(.96)}
.btn-final-approve{background:var(--approve);color:#fff}
.btn-final-approve:hover{background:#166534}
.btn-final-deny{background:var(--deny);color:#fff}
.btn-final-deny:hover{background:#b91c1c}
.btn-final-approve.chosen{box-shadow:0 0 0 3px var(--approve-bg)}
.btn-final-deny.chosen{box-shadow:0 0 0 3px var(--deny-bg)}

/* drawer */
#drawer{
  background:var(--surface);border-top:1px solid var(--border);
  transition:max-height .3s ease;overflow:hidden;max-height:48px;
  flex-shrink:0;
}
#drawer.open{max-height:70dvh;overflow-y:auto}
#drawer-toggle{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 16px;cursor:pointer;-webkit-tap-highlight-color:transparent;
  font-weight:600;font-size:.85rem;color:var(--text);
}
#drawer-toggle span:last-child{font-size:1.1rem;transition:transform .3s}
#drawer.open #drawer-toggle span:last-child{transform:rotate(180deg)}
#drawer-body{padding:0 16px 16px}

#snippet-preview{
  font-size:.78rem;color:var(--text2);font-style:italic;
  background:var(--bg);padding:8px 10px;border-radius:6px;
  margin-bottom:8px;display:none;word-break:break-word;
  border-left:3px solid var(--accent);
}
#snippet-preview.visible{display:block}
#comment-input{
  width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);
  border-radius:6px;color:var(--text);font:inherit;font-size:.9rem;resize:none;
  min-height:44px;max-height:120px;
}
#comment-input:focus{outline:none;border-color:var(--accent)}
#submit-comment{
  margin-top:8px;width:100%;padding:10px;background:var(--accent);color:#fff;
  border:none;border-radius:6px;font-weight:600;font-size:.9rem;cursor:pointer;
}
#submit-comment:active{transform:scale(.98)}
#submit-comment:disabled{opacity:.3;cursor:default}

.comment{
  background:var(--bg);border:1px solid var(--border);border-radius:8px;
  padding:10px 12px;margin-top:10px;
}
.comment-snippet{
  font-size:.78rem;color:var(--text2);font-style:italic;
  margin-bottom:5px;padding-left:8px;border-left:2px solid var(--text2);
}
.comment-body{font-size:.88rem;line-height:1.5}
.comment-meta{font-size:.72rem;color:var(--text2);margin-top:5px}
.comment-section-tag{
  display:inline-block;font-size:.68rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.04em;background:var(--surface2);color:var(--text2);
  padding:1px 6px;border-radius:3px;margin-bottom:4px;
}
.comment-actions{display:flex;gap:8px;margin-top:6px}
.comment-actions button{
  background:none;border:none;color:var(--text2);font-size:.72rem;cursor:pointer;
  padding:2px 0;text-decoration:underline;
}
.comment-actions button:hover{color:var(--text)}
.comment-edit-area{width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);
  border-radius:6px;color:var(--text);font:inherit;font-size:.85rem;resize:vertical;
  min-height:40px;margin-top:6px;
}
.comment-edit-actions{display:flex;gap:8px;margin-top:6px}
.comment-edit-actions button{
  padding:5px 12px;font-size:.78rem;font-weight:600;border-radius:4px;border:none;cursor:pointer;
}
.comment-edit-actions .save-btn{background:var(--accent);color:#fff}
.comment-edit-actions .cancel-btn{background:var(--surface2);color:var(--text)}

@media(min-width:768px){
  #app{flex-direction:row}
  #plan{flex:1}
  #drawer{
    width:380px;max-height:none;height:100dvh;overflow-y:auto;
    border-top:none;border-left:1px solid var(--border);
  }
  #drawer,#drawer.open{max-height:none}
  #drawer-toggle{display:none}
  #drawer-body{padding:16px;display:block!important}
  #fab{right:400px}
  #fab-menu{right:400px}
}
</style>
</head>
<body>
<div id="app">
  <div id="plan">
    <div id="plan-inner"></div>
  </div>
  <button id="fab" onclick="toggleFabMenu()" aria-label="Final verdict">&#x2714;</button>
  <div id="fab-menu">
    <button class="btn-final-approve" onclick="finalVerdict('approved')">Approve Entire Plan</button>
    <button class="btn-final-deny" onclick="finalVerdict('denied')">Deny Plan</button>
  </div>
  <div id="drawer">
    <div id="drawer-toggle" onclick="toggleDrawer()">
      <span id="drawer-label">Comments (0)</span>
      <span>&#9650;</span>
    </div>
    <div id="drawer-body">
      <div id="snippet-preview"></div>
      <textarea id="comment-input" placeholder="Add a comment..." rows="2"></textarea>
      <button id="submit-comment" onclick="submitComment()" disabled>Post Comment</button>
      <div id="comment-list"></div>
    </div>
  </div>
</div>
<script>
let comments = [];
let approvals = {};
let selectedSnippet = "";
let nearestSection = "";

(async function init() {
  const [planText, commentsData, approvalsData] = await Promise.all([
    fetch("/api/plan").then(r => r.text()),
    fetch("/api/comments").then(r => r.json()),
    fetch("/api/approvals").then(r => r.json()),
  ]);
  comments = commentsData;
  approvals = approvalsData;
  renderPlan(planText);
  renderComments();
  setupSelection();
  syncFinalVerdict();
})();

function renderPlan(md) {
  const el = document.getElementById("plan-inner");
  el.innerHTML = marked.parse(md);
  el.querySelectorAll("h2").forEach(h2 => {
    const sectionId = h2.textContent.trim();
    const row = document.createElement("div");
    row.className = "approval-row";
    row.innerHTML =
      `<button data-action="approved" onclick="event.stopPropagation();toggleApproval(this,'${esc(sectionId)}','approved')">Approve</button>` +
      `<button data-action="denied" onclick="event.stopPropagation();toggleApproval(this,'${esc(sectionId)}','denied')">Deny</button>`;
    h2.appendChild(row);
    syncApprovalUI(h2, sectionId);
  });
}

function esc(s) { return s.replace(/\\/g,"\\\\").replace(/'/g,"\\'"); }

function syncApprovalUI(h2, sectionId) {
  const a = approvals[sectionId];
  h2.querySelectorAll(".approval-row button").forEach(btn => {
    btn.className = "";
    if (a && a.status === btn.dataset.action) {
      btn.className = btn.dataset.action === "approved" ? "on-approve" : "on-deny";
    }
  });
}

async function toggleApproval(btn, sectionId, status) {
  const current = approvals[sectionId];
  const newStatus = (current && current.status === status) ? "pending" : status;
  const res = await fetch("/api/approvals", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: sectionId, status: newStatus}),
  });
  approvals[sectionId] = await res.json();
  syncApprovalUI(btn.closest("h2"), sectionId);
}

// --- FAB + final verdict ---
function toggleFabMenu() {
  document.getElementById("fab-menu").classList.toggle("open");
}
document.addEventListener("click", function(e) {
  if (!e.target.closest("#fab") && !e.target.closest("#fab-menu")) {
    document.getElementById("fab-menu").classList.remove("open");
  }
});

async function finalVerdict(status) {
  const current = approvals["__final__"];
  const newStatus = (current && current.status === status) ? "pending" : status;
  const res = await fetch("/api/approvals", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: "__final__", status: newStatus}),
  });
  approvals["__final__"] = await res.json();
  syncFinalVerdict();
  document.getElementById("fab-menu").classList.remove("open");
}

function syncFinalVerdict() {
  const f = approvals["__final__"];
  document.querySelector(".btn-final-approve").className = "btn-final-approve" + (f && f.status === "approved" ? " chosen" : "");
  document.querySelector(".btn-final-deny").className = "btn-final-deny" + (f && f.status === "denied" ? " chosen" : "");
}

// --- text selection ---
function setupSelection() {
  const plan = document.getElementById("plan");

  function handleSelectionEnd(e) {
    // ignore clicks on buttons
    if (e.target.closest && e.target.closest("button")) return;
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : "";
    if (text.length < 3) return;
    selectedSnippet = text.substring(0, 300);
    nearestSection = findNearestSection(sel);
    showSnippetPreview(selectedSnippet);
    openDrawer();
  }

  plan.addEventListener("mouseup", handleSelectionEnd);
  plan.addEventListener("touchend", (e) => setTimeout(() => handleSelectionEnd(e), 150));
}

function findNearestSection(sel) {
  if (!sel || !sel.anchorNode) return "";
  let node = sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentElement : sel.anchorNode;
  while (node && node.id !== "plan-inner") {
    let sib = node.previousElementSibling;
    while (sib) {
      if (/^H[12]$/.test(sib.tagName)) {
        // strip button text from the heading
        const clone = sib.cloneNode(true);
        clone.querySelectorAll(".approval-row").forEach(r => r.remove());
        return clone.textContent.trim();
      }
      sib = sib.previousElementSibling;
    }
    node = node.parentElement;
  }
  return "";
}

function showSnippetPreview(text) {
  const el = document.getElementById("snippet-preview");
  el.textContent = "\u201c" + text + "\u201d";
  el.classList.add("visible");
  document.getElementById("submit-comment").disabled = !document.getElementById("comment-input").value.trim();
}

function toggleDrawer() { document.getElementById("drawer").classList.toggle("open"); }
function openDrawer() { document.getElementById("drawer").classList.add("open"); }

document.getElementById("comment-input").addEventListener("input", function() {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 120) + "px";
  document.getElementById("submit-comment").disabled = !this.value.trim();
});

async function submitComment() {
  const body = document.getElementById("comment-input").value.trim();
  if (!body) return;
  const res = await fetch("/api/comments", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({snippet: selectedSnippet, body, section: nearestSection}),
  });
  comments.push(await res.json());
  document.getElementById("comment-input").value = "";
  document.getElementById("comment-input").style.height = "auto";
  document.getElementById("snippet-preview").classList.remove("visible");
  selectedSnippet = "";
  document.getElementById("submit-comment").disabled = true;
  renderComments();
}

function renderComments() {
  const el = document.getElementById("comment-list");
  document.getElementById("drawer-label").textContent = "Comments (" + comments.length + ")";
  el.innerHTML = comments.slice().reverse().map(c =>
    '<div class="comment" id="comment-' + c.id + '">' +
      (c.section ? '<div class="comment-section-tag">' + escHtml(c.section) + '</div>' : '') +
      (c.snippet ? '<div class="comment-snippet">' + escHtml(c.snippet) + '</div>' : '') +
      '<div class="comment-body">' + escHtml(c.body) + '</div>' +
      '<div class="comment-meta">' + timeAgo(c.ts) + '</div>' +
      '<div class="comment-actions">' +
        '<button onclick="startEdit(' + c.id + ')">Edit</button>' +
        '<button onclick="deleteComment(' + c.id + ')">Delete</button>' +
      '</div>' +
    '</div>'
  ).join("");
}

function startEdit(id) {
  const c = comments.find(x => x.id === id);
  if (!c) return;
  const el = document.getElementById("comment-" + id);
  const bodyEl = el.querySelector(".comment-body");
  const actionsEl = el.querySelector(".comment-actions");
  bodyEl.innerHTML = '<textarea class="comment-edit-area">' + escHtml(c.body) + '</textarea>' +
    '<div class="comment-edit-actions">' +
      '<button class="save-btn" onclick="saveEdit(' + id + ',this)">Save</button>' +
      '<button class="cancel-btn" onclick="renderComments()">Cancel</button>' +
    '</div>';
  actionsEl.style.display = "none";
  el.querySelector(".comment-edit-area").focus();
}

async function saveEdit(id, btn) {
  const textarea = btn.closest(".comment-body").querySelector("textarea");
  const body = textarea.value.trim();
  if (!body) return;
  await fetch("/api/comments/" + id, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({body, snippet: "", section: ""}),
  });
  const c = comments.find(x => x.id === id);
  if (c) c.body = body;
  renderComments();
}

async function deleteComment(id) {
  if (!confirm("Delete this comment?")) return;
  await fetch("/api/comments/" + id, {method: "DELETE"});
  comments = comments.filter(c => c.id !== id);
  renderComments();
}

function escHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

function timeAgo(iso) {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff/60) + "m ago";
  if (diff < 86400) return Math.floor(diff/3600) + "h ago";
  return new Date(iso).toLocaleDateString();
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
