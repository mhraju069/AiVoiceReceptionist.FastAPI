import html
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from database import get_db
from models.activity_models import CallLog
from models.auth_models import User
from services.rag_service import load_knowledge, save_knowledge
from services.prompts import (
    load_full_prompt_template,
    load_greetings,
    save_full_prompt_template,
    save_greetings,
)
from services.known_clients import (
    delete_known_client,
    list_known_clients,
    upsert_known_client,
)
from utils.auth_utils import create_access_token, decode_access_token, verify_password


router = APIRouter(prefix="/admin", tags=["Admin"])


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _admin_layout(title: str, body: str, current_user: Optional[User] = None) -> HTMLResponse:
    user_label = _e(current_user.email) if current_user else ""
    auth_nav = (
        f'<span class="user">{user_label}</span><a href="/admin/logout">Logout</a>'
        if current_user else ""
    )
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)} | AI Receptionist Admin</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #18212f;
      --muted: #667085;
      --line: #dde3ea;
      --accent: #0f766e;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    header {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 24px; border-bottom: 1px solid var(--line); background: var(--panel); position: sticky; top: 0; z-index: 2; }}
    h1 {{ font-size: 20px; margin: 0; }}
    h2 {{ font-size: 16px; margin: 0 0 14px; }}
    nav {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }}
    nav a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    main {{ max-width: 1180px; margin: 24px auto; padding: 0 18px 40px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    .toolbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
    .grid {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
    label {{ display: grid; gap: 6px; color: var(--muted); font-size: 13px; }}
    input, select, textarea {{ width: 100%; padding: 10px 11px; border: 1px solid var(--line); border-radius: 6px; background: white; font: inherit; color: var(--text); }}
    textarea {{ min-height: 76px; resize: vertical; }}
    button, .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 38px; padding: 8px 13px; border: 0; border-radius: 6px; background: var(--accent); color: white; font-weight: 700; text-decoration: none; cursor: pointer; }}
    .danger {{ background: var(--danger); }}
    .muted {{ color: var(--muted); }}
    .user {{ color: var(--muted); }}
    .error {{ color: var(--danger); margin-bottom: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .clip {{ max-width: 360px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .actions {{ display: flex; gap: 8px; align-items: center; }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>AI Receptionist Admin</h1>
    <nav>
      <a href="/admin/calls">Calls</a>
      <a href="/admin/company">Company Data</a>
      <a href="/admin/prompts">Prompts</a>
      <a href="/admin/known-clients">Known Clients</a>
      <a href="/admin/users">Users</a>
      {auth_nav}
    </nav>
  </header>
  <main>{body}</main>
</body>
</html>""")


def get_admin_user(
    admin_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not admin_token:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/admin/login"})

    payload = decode_access_token(admin_token)
    email = payload.get("sub") if payload else None
    if not email:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/admin/login"})

    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/admin/login"})
    return user


@router.get("", include_in_schema=False)
def admin_home():
    return RedirectResponse("/admin/calls", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/", include_in_schema=False)
def admin_home_slash():
    return RedirectResponse("/admin/calls", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def admin_login_page(error: Optional[str] = None):
    error_html = f'<div class="error">{_e(error)}</div>' if error else ""
    body = f"""
    <section class="panel" style="max-width:420px;margin:70px auto 0;">
      <h2>Admin Login</h2>
      {error_html}
      <form method="post" action="/admin/login">
        <label>Email<input name="email" type="email" autocomplete="username" required></label>
        <br>
        <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
        <br>
        <button type="submit">Login</button>
      </form>
    </section>
    """
    return _admin_layout("Login", body)


@router.post("/login", include_in_schema=False)
def admin_login(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active or not verify_password(password, user.hashed_password):
        return RedirectResponse(
            f"/admin/login?error={quote('Invalid email or password')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    token = create_access_token(data={"sub": user.email}, expires_delta=timedelta(hours=8))
    redirect = RedirectResponse("/admin/calls", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        "admin_token",
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=8 * 60 * 60,
    )
    return redirect


@router.get("/logout", include_in_schema=False)
def admin_logout():
    redirect = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie("admin_token")
    return redirect


@router.get("/calls", response_class=HTMLResponse, include_in_schema=False)
def admin_calls(
    page: int = 1,
    page_size: int = 25,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 5), 100)
    total = db.query(CallLog).count()
    calls = (
        db.query(CallLog)
        .order_by(desc(CallLog.start_time))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    rows = "".join(
        f"""<tr>
          <td>{call.id}</td>
          <td>{_e(call.caller_number)}</td>
          <td>{_e(call.start_time)}</td>
          <td>{_e(call.duration)}</td>
          <td>{_e(call.reason)}</td>
          <td>{_e(call.status)}</td>
          <td class="clip" title="{_e(call.summary)}">{_e(call.summary)}</td>
        </tr>"""
        for call in calls
    )
    body = f"""
    <section class="panel">
      <div class="toolbar">
        <h2>Call Logs</h2>
        <span class="muted">{total} total</span>
      </div>
      <table>
        <thead><tr><th>ID</th><th>Caller</th><th>Started</th><th>Duration</th><th>Reason</th><th>Status</th><th>Summary</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="7" class="muted">No calls yet.</td></tr>'}</tbody>
      </table>
      <div class="toolbar" style="margin-top:14px;">
        <a class="button" href="/admin/calls?page={max(page - 1, 1)}&page_size={page_size}">Previous</a>
        <span class="muted">Page {page}</span>
        <a class="button" href="/admin/calls?page={page + 1}&page_size={page_size}">Next</a>
      </div>
    </section>
    """
    return _admin_layout("Calls", body, current_user)


@router.get("/users", response_class=HTMLResponse, include_in_schema=False)
def admin_users(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    rows = "".join(
        f"""<tr>
          <td>{user.id}</td>
          <td>{_e(user.name)}</td>
          <td>{_e(user.email)}</td>
          <td>{'Active' if user.is_active else 'Inactive'}</td>
          <td>{_e(user.created_at)}</td>
        </tr>"""
        for user in users
    )
    body = f"""
    <section class="panel">
      <div class="toolbar">
        <h2>Users</h2>
        <span class="muted">{len(users)} total</span>
      </div>
      <table>
        <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Status</th><th>Created</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="5" class="muted">No users found.</td></tr>'}</tbody>
      </table>
    </section>
    """
    return _admin_layout("Users", body, current_user)


@router.get("/company", response_class=HTMLResponse, include_in_schema=False)
def admin_company(
    saved: Optional[str] = None,
    current_user: User = Depends(get_admin_user),
):
    knowledge = load_knowledge()
    saved_html = '<div class="panel" style="border-color:#99d6c9;color:#0f766e;">Company data saved. New AI calls will use the updated details.</div>' if saved else ""
    body = f"""
    {saved_html}
    <section class="panel">
      <div class="toolbar">
        <div>
          <h2>Company Data For AI</h2>
          <p class="muted" style="margin:4px 0 0;">This is the source of truth Reba uses for company details, services, staff, pricing policy, office hours, booking rules, and FAQs.</p>
        </div>
      </div>
      <form method="post" action="/admin/company">
        <label>Knowledge Base
          <textarea name="knowledge" style="min-height:620px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;line-height:1.45;" required>{_e(knowledge)}</textarea>
        </label>
        <br>
        <button type="submit">Save Company Data</button>
      </form>
    </section>
    """
    return _admin_layout("Company Data", body, current_user)


@router.post("/company", include_in_schema=False)
def admin_save_company(
    knowledge: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    save_knowledge(knowledge)
    return RedirectResponse("/admin/company?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/prompts", response_class=HTMLResponse, include_in_schema=False)
def admin_prompts(
    saved: Optional[str] = None,
    error: Optional[str] = None,
    current_user: User = Depends(get_admin_user),
):
    greetings_text = "\n".join(load_greetings())
    prompt_template = load_full_prompt_template()
    saved_html = '<div class="panel" style="border-color:#99d6c9;color:#0f766e;">Prompt settings saved. New AI calls will use the updated prompt and greetings.</div>' if saved else ""
    error_html = f'<div class="panel error">{_e(error)}</div>' if error else ""
    body = f"""
    {saved_html}
    {error_html}
    <section class="panel">
      <div class="toolbar">
        <div>
          <h2>Greetings And Full Prompt</h2>
          <p class="muted" style="margin:4px 0 0;">Edit one greeting per line. The AI randomly chooses one for new prospect calls.</p>
        </div>
      </div>
      <form method="post" action="/admin/prompts">
        <label>Greetings
          <textarea name="greetings" style="min-height:180px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;line-height:1.45;" required>{_e(greetings_text)}</textarea>
        </label>
        <br>
        <label>Full Prompt Template
          <textarea name="prompt_template" style="min-height:720px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;line-height:1.45;" required>{_e(prompt_template)}</textarea>
        </label>
        <p class="muted">
          Available placeholders:
          <code>{{current_time}}</code>,
          <code>{{office_timezone}}</code>,
          <code>{{selected_greeting}}</code>,
          <code>{{knowledge}}</code>.
        </p>
        <button type="submit">Save Prompt Settings</button>
      </form>
    </section>
    """
    return _admin_layout("Prompts", body, current_user)


@router.post("/prompts", include_in_schema=False)
def admin_save_prompts(
    greetings: str = Form(...),
    prompt_template: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    try:
        save_greetings(greetings)
        save_full_prompt_template(prompt_template)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/prompts?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/admin/prompts?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/known-clients", response_class=HTMLResponse, include_in_schema=False)
def admin_known_clients(current_user: User = Depends(get_admin_user)):
    clients = list_known_clients()
    rows = "".join(
        f"""<tr>
          <td>{_e(client.get('plan'))}</td>
          <td>{_e(client.get('first_name'))} {_e(client.get('last_name'))}</td>
          <td>{_e(client.get('phone'))}</td>
          <td>{_e(client.get('email'))}</td>
          <td>{_e(client.get('business_name'))}</td>
          <td>{_e(client.get('notes'))}</td>
          <td>
            <form method="post" action="/admin/known-clients/delete" onsubmit="return confirm('Delete this client?')">
              <input type="hidden" name="phone" value="{_e(client.get('phone'))}">
              <button class="danger" type="submit">Delete</button>
            </form>
          </td>
        </tr>"""
        for client in clients
    )
    body = f"""
    <section class="panel">
      <h2>Add Or Update Known Client</h2>
      <form method="post" action="/admin/known-clients">
        <div class="grid">
          <label>Plan
            <select name="plan">
              <option>None</option><option>A</option><option>B</option><option>C</option><option>D</option>
            </select>
          </label>
          <label>First Name<input name="first_name" required></label>
          <label>Last Name<input name="last_name"></label>
          <label>Phone<input name="phone" required></label>
          <label>Email<input name="email" type="email"></label>
          <label>Business Name<input name="business_name"></label>
        </div>
        <br>
        <label>Notes<textarea name="notes"></textarea></label>
        <br>
        <button type="submit">Save Client</button>
      </form>
    </section>
    <section class="panel">
      <div class="toolbar">
        <h2>Known Clients</h2>
        <span class="muted">{len(clients)} total</span>
      </div>
      <table>
        <thead><tr><th>Plan</th><th>Name</th><th>Phone</th><th>Email</th><th>Business</th><th>Notes</th><th></th></tr></thead>
        <tbody>{rows or '<tr><td colspan="7" class="muted">No known clients yet.</td></tr>'}</tbody>
      </table>
    </section>
    """
    return _admin_layout("Known Clients", body, current_user)


@router.post("/known-clients", include_in_schema=False)
def admin_save_known_client(
    plan: str = Form("None"),
    first_name: str = Form(""),
    last_name: str = Form(""),
    phone: str = Form(...),
    email: str = Form(""),
    business_name: str = Form(""),
    notes: str = Form(""),
    current_user: User = Depends(get_admin_user),
):
    upsert_known_client({
        "plan": plan,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": email,
        "business_name": business_name,
        "notes": notes,
    })
    return RedirectResponse("/admin/known-clients", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/known-clients/delete", include_in_schema=False)
def admin_delete_known_client(
    phone: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    delete_known_client(phone)
    return RedirectResponse("/admin/known-clients", status_code=status.HTTP_303_SEE_OTHER)
