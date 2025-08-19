import os
import re
import json
import random
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo  # <- para formatar data em America/Recife
import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
IG_USER = os.getenv("INSTAGRAM_USER", "mcdonalds_br").strip().lstrip("@")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
STATE_FILE = f".last_{IG_USER}.json"
SEND_BOOTSTRAP = os.getenv("SEND_BOOTSTRAP", "false").lower() in ("1","true","yes","y")
IG_SESSIONID = os.getenv("IG_SESSIONID", "").strip()
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("1","true","yes","y")
ONLY_ONCE_PER_DAY = os.getenv("ONLY_ONCE_PER_DAY", "false").lower() in ("1","true","yes","y")  # <- opcional
MURAL_ROLE_ID = os.getenv("MURAL_ROLE_ID", "").strip()

assert DISCORD_WEBHOOK, "Coloque sua URL de webhook no .env (DISCORD_WEBHOOK=...)"

PROFILE_URL = f"https://www.instagram.com/{IG_USER}/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# ====================== Estado ======================
def load_state():
    defaults = {
        "last_shortcode": None,
        "last_dt_iso": None,
        "bootstrapped": False,
        "last_notified_date": None,  # <- guardamos o último dia (em Recife) que notificou
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in defaults:
                if k in saved and saved[k] is not None:
                    defaults[k] = saved[k]
        except Exception:
            pass
    return defaults

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

# ====================== Util ======================
def post_url_from_shortcode(shortcode: str) -> str:
    return f"https://www.instagram.com/p/{shortcode}/"

def extract_shortcode(url: str):
    m = re.search(r"/(p|reel)/([A-Za-z0-9_-]+)/", url)
    return m.group(2) if m else None

def iso_to_recife_date_str(iso_str: str | None) -> str:
    """Converte ISO (UTC) para string DD/MM/AAAA em America/Recife."""
    try:
        if iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        else:
            dt = datetime.utcnow()
        dt_local = dt.astimezone(ZoneInfo("America/Recife"))
        return dt_local.strftime("%d/%m/%Y")
    except Exception:
        return "data desconhecida"

def iso_to_recife_datetime_str(iso_str: str | None) -> str:
    """Converte ISO (UTC) para DD/MM/AAAA HH:MM em America/Recife, com fallback UTC-3."""
    try:
        if iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.now(timezone.utc)

        try:
            tz_recife = ZoneInfo("America/Recife")
        except Exception:
            tz_recife = timezone(timedelta(hours=-3))

        return dt.astimezone(tz_recife).strftime("%d/%m/%Y %H:%M")
    except Exception as e:
        print(f"[WARN] Falha formatando data/hora: {e}")
        return "data desconhecida"


# ====================== Envio pro Discord ======================
def send_to_discord(shortcode: str, taken_at_iso: str | None):
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo

    MURAL_ROLE_ID = os.getenv("MURAL_ROLE_ID", "").strip()

    # Só envia se houver shortcode válido
    if not shortcode or str(shortcode).lower() == "none":
        print("[WARN] Shortcode vazio; não vou enviar nada ao Discord.")
        return

    # Formata data/hora em America/Recife
    def iso_to_recife_datetime_str(iso_str: str | None) -> str:
        try:
            if iso_str:
                dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = datetime.now(timezone.utc)
            try:
                tz_recife = ZoneInfo("America/Recife")
            except Exception:
                tz_recife = timezone(timedelta(hours=-3))
            return dt.astimezone(tz_recife).strftime("%d/%m/%Y %H:%M")
        except Exception as e:
            print(f"[WARN] Falha formatando data/hora: {e}")
            return "data desconhecida"

    post_url = post_url_from_shortcode(shortcode)
    data_hora_br = iso_to_recife_datetime_str(taken_at_iso)

    # Se tiver ID, menciona a role de verdade; senão cai no texto @Mural
    mention = f"<@&{MURAL_ROLE_ID}>" if MURAL_ROLE_ID else "@Mural"

    content = (
        "🚨 Alerta Publicação Nova!\n\n"
        f"({data_hora_br})\n\n"
        f"{mention}\n"
        f"{post_url}"
    )

    payload = {"content": content}
    if MURAL_ROLE_ID:
        # Permite explicitamente mencionar essa role
        payload["allowed_mentions"] = {"roles": [MURAL_ROLE_ID]}

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        r.raise_for_status()
        print(f"[{datetime.now()}] Enviado ao Discord: {post_url}")
    except Exception as e:
        print(f"[ERRO] Falha ao enviar pro Discord: {e}")

# ====================== Playwright helpers ======================
async def close_cookie_modals(page):
    selectors = [
        'button:has-text("Allow all")',
        'button:has-text("Accept All")',
        'button:has-text("Permitir todos")',
        'button:has-text("Aceitar")',
        'div[role="dialog"] button:has-text("Ok")',
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await page.wait_for_timeout(400)
        except:
            pass

async def get_grid_links(page):
    try:
        await page.wait_for_selector("article", timeout=15000)
    except:
        print("[WARN] Grid não carregou (article ausente).")
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)
    except:
        pass
    links = await page.evaluate("""
        () => Array.from(document.querySelectorAll("a[href*='/p/'], a[href*='/reel/']"))
                    .map(a => a.href)
    """)
    seen, ordered = set(), []
    for u in links:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    print(f"[DEBUG] Links capturados no grid: {len(ordered)}")
    return ordered[:15]

async def fetch_post_datetime(context, url: str):
    sc = extract_shortcode(url)
    if not sc:
        return None, None
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time_el = await page.query_selector("time[datetime]") or await page.query_selector("//time[@datetime]")
        if not time_el:
            return sc, None
        dt_iso = await time_el.get_attribute("datetime")
        if not dt_iso:
            return sc, None
        try:
            taken_at = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
        except Exception:
            taken_at = None
        return sc, taken_at
    except PlaywrightTimeout:
        return sc, None
    finally:
        await page.close()

async def fetch_latest_by_datetime(playwright):
    browser = await playwright.chromium.launch(headless=HEADLESS)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="pt-BR",
        extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"},
    )

    # Se tiver cookie de sessão, injeta (para contornar login wall / perfil privado)
    if IG_SESSIONID:
        try:
            await context.add_cookies([{
                "name": "sessionid",
                "value": IG_SESSIONID,
                "domain": ".instagram.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            }])
            print("[INFO] Cookie de sessão adicionado ao contexto.")
        except Exception as e:
            print(f"[WARN] Falha ao adicionar IG_SESSIONID: {e}")

    page = await context.new_page()
    try:
        print("[DEBUG] Abrindo perfil:", PROFILE_URL)
        await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
        print("[DEBUG] URL após goto:", page.url)

        # Detecta login wall
        html = await page.content()
        if "accounts/login" in page.url or any(t in html for t in ["Log in", "Entrar", "Iniciar sessão"]):
            print("[ALERTA] Instagram pediu login. Preencha IG_SESSIONID no .env para continuar.")
            return None, None

        # Fecha possíveis pop-ups
        await close_cookie_modals(page)

        # Tenta achar links do grid (com algumas roladas)
        links = []
        for i in range(4):
            batch = await page.evaluate("""
                () => Array.from(document.querySelectorAll("a[href*='/p/'], a[href*='/reel/']"))
                            .map(a => a.href)
            """)
            # Remove duplicatas mantendo ordem
            seen = set()
            ordered = []
            for u in batch:
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)
            for u in ordered:
                if u not in links:
                    links.append(u)

            if len(links) >= 6:
                break

            # scroll e espera novo conteúdo
            try:
                await page.mouse.wheel(0, 1200)
            except:
                pass
            await page.wait_for_timeout(900)

        print(f"[DEBUG] Links capturados no grid: {len(links)}")
        if not links:
            print("[WARN] Nenhum link /p/ ou /reel/ encontrado no grid.")
            return None, None

        # Abre alguns candidatos e pega a data real
        results = []
        for url in links[:12]:
            sc, taken_at = await fetch_post_datetime(context, url)
            if sc:
                results.append((sc, taken_at))

        with_dates = [(sc, dt) for sc, dt in results if dt is not None]
        if not with_dates:
            sc_first = extract_shortcode(links[0]) if links else None
            print("[WARN] Nenhuma data encontrada nos posts; usando o primeiro do grid como fallback.")
            return sc_first, None

        with_dates.sort(key=lambda x: x[1], reverse=True)  # mais novo primeiro
        latest_sc, latest_dt = with_dates[0]
        return latest_sc, latest_dt

    finally:
        await context.close()
        await browser.close()

# ====================== Main loop ======================
async def main():
    state = load_state()
    print(f"Monitorando @{IG_USER} → {PROFILE_URL}")
    print(f"Checando a cada ~{CHECK_INTERVAL}s. Para parar, Ctrl+C.\n")

    async with async_playwright() as p:
        while True:
            try:
                shortcode, taken_at = await fetch_latest_by_datetime(p)
                ts_iso = taken_at.isoformat() if taken_at else None

                if not shortcode:
                    print(f"[{datetime.now()}] Nenhum post encontrado (tentará de novo).")
                else:
                    if not state.get("bootstrapped"):
                        # Primeira execução: marca e (se quiser) envia 1x
                        state["last_shortcode"] = shortcode
                        state["last_dt_iso"] = ts_iso
                        state["bootstrapped"] = True
                        save_state(state)
                        print(f"[BOOTSTRAP] Último existente marcado: {shortcode} ({ts_iso or 'sem data'})")
                        if SEND_BOOTSTRAP and shortcode:
                            # respeita ONLY_ONCE_PER_DAY se estiver ligado
                            dt_local_str = iso_to_recife_date_str(ts_iso)
                            if not ONLY_ONCE_PER_DAY or state.get("last_notified_date") != dt_local_str:
                                send_to_discord(shortcode, ts_iso)
                                state["last_notified_date"] = dt_local_str
                                save_state(state)
                    else:
                        # Compara por shortcode e por data
                        last_dt = None
                        if state.get("last_dt_iso"):
                            try:
                                last_dt = datetime.fromisoformat(state["last_dt_iso"].replace("Z", "+00:00"))
                            except Exception:
                                last_dt = None

                        is_new_shortcode = (state.get("last_shortcode") != shortcode)
                        is_newer_by_time = (taken_at is not None and (last_dt is None or taken_at > last_dt))

                        if is_new_shortcode and is_newer_by_time:
                            dt_local_str = iso_to_recife_date_str(ts_iso)

                            # Se habilitado, só 1 alerta por dia (por data local do post)
                            if ONLY_ONCE_PER_DAY and state.get("last_notified_date") == dt_local_str:
                                print(f"[{datetime.now()}] Já notificado hoje ({dt_local_str}). Pulando envio.")
                            else:
                                print(f"[{datetime.now()}] Novo post detectado: {shortcode} ({ts_iso})")
                                send_to_discord(shortcode, ts_iso)
                                state["last_notified_date"] = dt_local_str

                            state["last_shortcode"] = shortcode
                            state["last_dt_iso"] = ts_iso
                            save_state(state)
                        else:
                            print(f"[{datetime.now()}] Não tem atualização agora. Último: {state.get('last_shortcode')} ({state.get('last_dt_iso')})")

            except Exception:
                print("[ERRO]\n" + traceback.format_exc())

            wait = CHECK_INTERVAL + random.randint(-5, 5)
            await asyncio.sleep(max(20, wait))  # nunca menos que 20s

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Saindo…")
