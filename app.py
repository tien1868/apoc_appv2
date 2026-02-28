"""
APOC² — Garment Intelligence Platform · Tags Technologies LLC
"""

from typing import List
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import anthropic
import base64, json, re, os, io, traceback, httpx, asyncio
import tempfile, threading, time, urllib.parse, uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ── Environment validation ──────────────────────────────────────────────────
def _validate_env():
    required = {
        "AWS_ACCESS_KEY_ID": "AWS Bedrock access",
        "AWS_SECRET_ACCESS_KEY": "AWS Bedrock access",
        "EBAY_APP_ID": "eBay API",
        "EBAY_CERT_ID": "eBay API",
        "EBAY_DEV_ID": "eBay API",
        "EBAY_RUNAME": "eBay OAuth redirect",
    }
    missing = [f"{k} ({v})" for k, v in required.items() if not os.environ.get(k)]
    if missing:
        print(f"⚠️  APOC² Warning — Missing env vars: {', '.join(missing)}")
        print("   Some features may not work correctly.")
    else:
        print("✅ APOC² — All environment variables configured")

_validate_env()

# ── Config ──────────────────────────────────────────────────────────────────
claude = anthropic.AnthropicBedrock(
    aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
    aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
    aws_region=os.environ.get("AWS_REGION", "us-east-1"),
)
CLAUDE_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"

EBAY_APP_ID  = os.environ.get("EBAY_APP_ID",  "")
EBAY_DEV_ID  = os.environ.get("EBAY_DEV_ID",  "")
EBAY_CERT_ID = os.environ.get("EBAY_CERT_ID", "")
EBAY_RUNAME  = os.environ.get("EBAY_RUNAME",  "")
EBAY_API_URL = "https://api.ebay.com/ws/api.dll"
FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

CATEGORY_MAP = {
    "men > sweaters > cardigan":"11484","men > sweaters > pullover":"11484",
    "men > sweaters":"11484","men > shirts > dress shirt":"57991",
    "men > shirts > t-shirt":"15687","men > shirts > casual":"57990",
    "men > shirts":"57990","men > jackets":"57988","men > coats":"57988",
    "men > vests":"15691","men > pants > jeans":"11483","men > jeans":"11483",
    "men > pants > dress":"57989","men > pants":"57989","men > shorts":"15689",
    "men > suits > blazer":"3002","men > suits":"3002","men > blazer":"3002",
    "men > activewear":"185101","men > swimwear":"15690",
    "women > sweaters":"63866","women > tops":"53159","women > blouses":"53159",
    "women > dresses":"63861","women > jackets":"63862","women > coats":"63862",
    "women > vests":"63862","women > pants > jeans":"11554","women > jeans":"11554",
    "women > pants":"63863","women > skirts":"63864","women > shorts":"11555",
    "women > activewear":"185099","women > swimwear":"63867",
    "default":"57990",
}

WEIGHT_MAP = {
    "t-shirt": (0, 8), "shirt": (0, 12), "dress shirt": (0, 14),
    "sweater": (1, 0), "cardigan": (1, 0), "hoodie": (1, 4),
    "jacket": (1, 8), "coat": (2, 8), "blazer": (1, 4),
    "vest": (0, 12), "pants": (1, 0), "jeans": (1, 8),
    "shorts": (0, 10), "dress": (0, 14), "skirt": (0, 10),
    "activewear": (0, 10), "swimwear": (0, 6),
}

def get_shipping_weight(category):
    cat_lower = (category or "").lower()
    for key, (lbs, oz) in WEIGHT_MAP.items():
        if key in cat_lower:
            return lbs, oz
    return 1, 0

SYSTEM_PROMPT = """You are APOC2, an expert AI garment analyst for eBay resale. Return ONLY valid JSON, no markdown, no code fences.

TAG READING PRIORITY — read tags FIRST before analyzing the garment:
1. Look at EVERY image for tags/labels (inside collar, side seam, hem, waistband, care labels)
2. SIZE: Read the EXACT text printed on the size tag. Look for S/M/L/XL/XXL, numeric sizes (32, 34, 40), or alpha-numeric (US 10, EU 48). Report EXACTLY what the tag says — never guess from garment appearance.
3. BRAND: Read the EXACT brand name from the main label. Check collar labels, hem tags, button engravings, zipper pulls.
4. STYLE NAME / MODEL: Read the specific product line, model, or style name from labels. This is CRITICAL for pricing. Examples: "Dickey" (Veronica Beard), "Atom SL" (Arc'teryx), "Storm System" (Loro Piana), "Patagonia Better Sweater", "Barbour Ashby", "Canada Goose Expedition". Look for secondary labels, interior tags, style numbers, and any text identifying the specific model. If you recognize the style from the garment's distinctive design even without a label, include it.
5. MATERIAL: Read the EXACT fabric composition from the care/content label (e.g. "60% Cotton 40% Polyester"). Report the full composition.
6. ORIGIN: Read "Made in ___" from the care label if visible.
7. CARE: Read care instructions from care label (e.g. "Machine wash cold", "Dry clean only").
8. If a tag is partially visible, blurry, or folded, report what you CAN read and note uncertainty.
9. If NO size tag is visible in any photo, set size to "" and confidence to "low".

Return this JSON structure:
{"title":"SEO title under 80 chars — Brand + Style Name + Type + Size + Color","title_alt1":"alternative SEO title emphasizing style/model name, under 80 chars","title_alt2":"alternative SEO title emphasizing keywords/features, under 80 chars","brand":"exact brand from tag","style_name":"specific model/product line name if identifiable (e.g. Dickey, Atom SL, Better Sweater) or null","sub_brand":null,"category":"Men > Sweaters > Cardigan","gender":"Men","size":"exact tag text","color":"Black","material":"60% Cotton 40% Polyester","style_details":["cable knit","ribbed cuffs","button front"],"sleeve_length":"Long Sleeve","neckline":"Crew Neck","pattern":"Solid","closure":"Button","fit":"Regular Fit","occasion":"Casual","season":"Fall/Winter","lining_material":null,"fabric_type":"Knit","accents":["Logo"],"theme":"Classic","collar_style":null,"cuff_style":null,"sleeve_type":null,"rise":null,"leg_style":null,"jacket_length":null,"dress_length":null,"character":null,"graphic_print":false,"handmade":false,"performance_activity":null,"insulation_material":null,"garment_care":"Machine wash cold","condition_score":4,"condition_label":"Good","condition_notes":"Light pilling on cuffs, minor fading at collar. No holes, stains, or structural damage.","defects_detected":["light pilling on cuffs","minor collar fading"],"description":"Detailed 4-6 sentence resale description. Describe the garment, its key features, material feel, condition, and who it suits. Write as a professional eBay seller — informative, accurate, and appealing.","features":["Cable knit texture throughout","Ribbed hem and cuffs","Genuine horn buttons","Reinforced shoulder seams"],"care_instructions":"Machine wash cold, tumble dry low","origin":"China","suggested_price_low":28,"suggested_price_high":45,"price_reasoning":"market reasoning with style name pricing if applicable","vintage":false,"vintage_era":null,"tags_present":false,"confidence":"high"}

TITLE RULES: Provide THREE title options. The main "title" should be brand-first (Brand + Style + Type + Size + Color). "title_alt1" should lead with the style/model name or key selling point. "title_alt2" should be keyword-rich for maximum search visibility. Each under 80 chars.

STYLE NAME RULES: This is the specific product line, model, or collection name — NOT generic descriptors. Examples of style names: "Dickey Jacket" (Veronica Beard), "Atom SL Hoody" (Arc'teryx), "Better Sweater" (Patagonia), "Ashby" (Barbour), "Expedition Parka" (Canada Goose), "Storm System" (Loro Piana), "Icon Trucker" (Levi's). If no specific style/model name is identifiable, set to null. Include the style name in the title and factor it into pricing — named styles typically command higher prices.

DESCRIPTION RULES: Write 4-6 sentences. Start with brand + garment type + key selling point. Mention material, fit, notable design details. State condition honestly. End with a styling suggestion or who it's ideal for.
FEATURES: List 3-6 notable design/construction features visible in photos (e.g. "Reinforced stitching", "Genuine leather trim", "Lined interior").
Condition: 1=NWT 2=Like New 3=Excellent 4=Good 5=Fair
sleeve_length must be one of: Long Sleeve, Short Sleeve, 3/4 Sleeve, Sleeveless, Cap Sleeve
neckline must be one of: Crew Neck, V-Neck, Round Neck, Scoop Neck, Turtleneck, Mock Neck, Collared, Hooded, Polo, Henley, Boat Neck, Cowl Neck, Square Neck, Off Shoulder, Strapless
pattern must be one of: Solid, Striped, Plaid, Paisley, Floral, Geometric, Abstract, Animal Print, Camouflage, Polka Dot, Colorblock, Herringbone, Houndstooth, Checkered/Gingham
closure must be one of: Button, Zip, Pull On, Snap, Hook & Eye, Tie, Buckle, Velcro, None
fit must be one of: Regular Fit, Slim Fit, Relaxed, Oversized, Athletic Fit, Classic, Tailored, Loose
occasion must be one of: Casual, Formal, Business, Active/Athletic, Special Occasion, Outdoor, Everyday, Lounge
season must be one of: All Seasons, Spring, Summer, Fall, Winter, Spring/Summer, Fall/Winter
fabric_type must be one of: Knit, Woven, Denim, Canvas, Jersey, Fleece, Terry, Twill, Satin, Chiffon, Lace, Mesh, Corduroy, Velvet, Flannel, Chambray, or null
accents: array of visible accents from: Logo, Embroidered, Zipper, Button, Patched, Rhinestone, Studded, Lace, Ruffle, Fringe, Sequined, Applique, Beaded, Monogram. Empty array [] if none.
theme must be one of: Classic, Bohemian, Modern, Nautical, Outdoor, College, Western, Hippie, Preppy, Streetwear, Minimalist, Retro, Grunge, Athleisure, or null
collar_style: Button-Down, Spread, Mandarin, Band, Point, Hooded, Shawl, Notched Lapel, Peak Lapel, Wing, or null. Only for shirts/jackets.
cuff_style: Barrel, French/Double, One Button, Ribbed, Elastic, or null. Only for dress shirts.
sleeve_type: Set-In, Raglan, Dolman, Bishop, Bell, Puff, Batwing, or null. Only if distinctive.
rise: Low Rise, Mid Rise, High Rise, or null. Only for pants/jeans/shorts.
leg_style: Straight, Slim, Skinny, Bootcut, Wide Leg, Tapered, Flare, Relaxed, Jogger, or null. Only for pants/jeans.
jacket_length: Short, Hip Length, Mid-Thigh, Knee Length, Long, or null. Only for jackets/coats.
dress_length: Short/Mini, Knee Length, Midi, Maxi/Full Length, Hi-Low, or null. Only for dresses/skirts.
character: Licensed character name if visible (Disney, Marvel, etc.) or null.
graphic_print: true only if garment has a graphic/screen print design.
performance_activity: Golf, Hiking, Running, Yoga, Training, Cycling, Fishing, Skiing, or null. Only for activewear/performance garments.
insulation_material: Down, Synthetic, Thinsulate, PrimaLoft, Fleece Lined, or null. Only for insulated outerwear.
garment_care: Read from care label. e.g. "Machine wash cold" or "Dry clean only". null if not visible."""

# ── State & sessions ────────────────────────────────────────────────────────
_pending_code = {"code": None, "error": None}
_sessions = {}
_sessions_lock = threading.Lock()
_ebay_token = None  # global fallback for single-user compat
_last_data = {}     # global fallback for single-user compat
FAL_KEY = os.environ.get("FAL_KEY", "")
_rembg_session = None
_rembg_failed = False
_rembg_error = ""
_start_time = datetime.utcnow()
_metrics = {"analyses": 0, "listings": 0, "bg_removals": 0, "errors": 0}

def get_session(sid):
    if not sid:
        return None
    with _sessions_lock:
        s = _sessions.get(sid)
        if s:
            s["last_used"] = datetime.utcnow()
        return s

def create_session():
    sid = str(uuid.uuid4())
    with _sessions_lock:
        _sessions[sid] = {
            "data": {}, "images": [], "ebay_token": None,
            "created": datetime.utcnow(), "last_used": datetime.utcnow(),
        }
    return sid

def cleanup_sessions():
    cutoff = datetime.utcnow() - timedelta(hours=2)
    with _sessions_lock:
        expired = [k for k, v in _sessions.items() if v["last_used"] < cutoff]
        for k in expired:
            for p in _sessions[k].get("images", []):
                try: os.unlink(p)
                except: pass
            del _sessions[k]

def _session_cleanup_loop():
    while True:
        time.sleep(1800)  # every 30 minutes
        cleanup_sessions()

threading.Thread(target=_session_cleanup_loop, daemon=True).start()

def _preload_rembg():
    global _rembg_session, _rembg_failed, _rembg_error
    try:
        from rembg import new_session
        _rembg_session = new_session("u2netp")
    except Exception as e:
        _rembg_failed, _rembg_error = True, str(e)

threading.Thread(target=_preload_rembg, daemon=True).start()

# ── App ─────────────────────────────────────────────────────────────────────
fapp = FastAPI()

fapp.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@fapp.get("/")
async def root():
    return JSONResponse({"name": "APOC²", "status": "running", "docs": "/docs", "health": "/health"})

# ── Health check & metrics ──────────────────────────────────────────────────
@fapp.get("/health")
async def health():
    uptime = (datetime.utcnow() - _start_time).total_seconds()
    return JSONResponse({
        "status": "healthy",
        "uptime_seconds": int(uptime),
        "rembg_ready": _rembg_session is not None,
        "ebay_configured": bool(EBAY_APP_ID),
        "claude_configured": bool(os.environ.get("AWS_ACCESS_KEY_ID")),
        "metrics": dict(_metrics),
        "active_sessions": len(_sessions),
    })

# ── eBay OAuth callback ────────────────────────────────────────────────────
@fapp.get("/ebay-callback", response_class=HTMLResponse)
async def ebay_callback(code: str = "", error: str = "", error_description: str = ""):
    global _pending_code
    if error:
        _pending_code = {"code": None, "error": error_description or error}
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>APOC²</title>
<style>body{{font-family:system-ui;background:#fafafa;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}.b{{text-align:center;padding:40px;background:white;border-radius:12px;border:1px solid #fecaca;max-width:420px;box-shadow:0 4px 24px rgba(0,0,0,.06)}}h2{{color:#dc2626;margin:0 0 12px}}p{{color:#6b7280}}</style></head>
<body><div class="b"><h2>Authorization Failed</h2><p>{error_description or error}</p><p style="margin-top:16px;font-size:13px;color:#9ca3af;">Close this tab and try again.</p></div></body></html>""")
    _pending_code = {"code": code, "error": None}
    return HTMLResponse("""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>APOC²</title>
<style>body{font-family:system-ui;background:#0d0f12;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;color:#e8e5e0}.b{text-align:center;padding:48px;background:#13161b;border-radius:12px;border:1px solid rgba(62,207,142,0.3);max-width:460px}h2{color:#3ecf8e;margin:0 0 10px}p{color:#7a7875;line-height:1.7;margin:0 0 20px}.spin{display:inline-block;width:20px;height:20px;border:2px solid #3ecf8e;border-top-color:transparent;border-radius:50%;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}</style>
</head><body><div class="b"><h2>eBay Authorized ✓</h2><p>Return to APOC² and tap <b>Complete Authorization</b></p><div class="spin"></div></div></body></html>""")

# ── eBay auth URL ───────────────────────────────────────────────────────────
@fapp.get("/ebay-auth-url")
async def ebay_auth_url():
    if not EBAY_APP_ID or not EBAY_RUNAME:
        return JSONResponse({"error": "eBay credentials not configured"}, status_code=500)
    scopes = " ".join([
        "https://api.ebay.com/oauth/api_scope",
        "https://api.ebay.com/oauth/api_scope/sell.inventory",
        "https://api.ebay.com/oauth/api_scope/sell.account",
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    ])
    url = "https://auth.ebay.com/oauth2/authorize?" + urllib.parse.urlencode({
        "client_id": EBAY_APP_ID, "redirect_uri": EBAY_RUNAME,
        "response_type": "code", "scope": scopes,
    })
    return JSONResponse({"url": url})

# ── Complete eBay OAuth ─────────────────────────────────────────────────────
@fapp.post("/ebay-complete")
async def ebay_complete_api(req: Request):
    global _pending_code, _ebay_token
    body = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    session_id = body.get("session_id", "")
    code = _pending_code.get("code")
    error = _pending_code.get("error")
    if error:
        return JSONResponse({"error": error}, status_code=400)
    if not code:
        return JSONResponse({"error": "No authorization code. Complete eBay sign-in first."}, status_code=400)
    try:
        creds = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.ebay.com/identity/v1/oauth2/token",
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "authorization_code", "code": code, "redirect_uri": EBAY_RUNAME},
                timeout=15)
        d = r.json()
        token = d.get("access_token")
        if not token:
            return JSONResponse({"error": d.get("error_description", "Token exchange failed")}, status_code=400)
        _ebay_token = token
        _pending_code = {"code": None, "error": None}
        # Store in session if provided
        sess = get_session(session_id)
        if sess:
            sess["ebay_token"] = token
        refresh = d.get("refresh_token", "")
        # Fetch seller policies
        h = {"Authorization": f"Bearer {token}"}
        b = "https://api.ebay.com/sell/account/v1"
        async with httpx.AsyncClient() as client:
            ship_r, ret_r, pay_r = await asyncio.gather(
                client.get(f"{b}/fulfillment_policy?marketplace_id=EBAY_US", headers=h, timeout=15),
                client.get(f"{b}/return_policy?marketplace_id=EBAY_US", headers=h, timeout=15),
                client.get(f"{b}/payment_policy?marketplace_id=EBAY_US", headers=h, timeout=15),
            )
        ship = ship_r.json()
        ret = ret_r.json()
        pay = pay_r.json()
        return JSONResponse({
            "connected": True,
            "access_token": token,
            "refresh_token": refresh,
            "shipping_policies": [{"name": p["name"], "id": p["fulfillmentPolicyId"]} for p in ship.get("fulfillmentPolicies", [])],
            "return_policies": [{"name": p["name"], "id": p["returnPolicyId"]} for p in ret.get("returnPolicies", [])],
            "payment_policies": [{"name": p["name"], "id": p["paymentPolicyId"]} for p in pay.get("paymentPolicies", [])],
        })
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Refresh eBay token ──────────────────────────────────────────────────────
@fapp.post("/ebay-refresh")
async def ebay_refresh_api(req: Request):
    global _ebay_token
    body = await req.json()
    rt = body.get("refresh_token")
    session_id = body.get("session_id", "")
    if not rt:
        return JSONResponse({"error": "No refresh token"}, status_code=400)
    try:
        creds = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
        scopes = "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.account https://api.ebay.com/oauth/api_scope/sell.fulfillment"
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.ebay.com/identity/v1/oauth2/token",
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "refresh_token", "refresh_token": rt, "scope": scopes},
                timeout=15)
        d = r.json()
        token = d.get("access_token")
        if not token:
            return JSONResponse({"error": d.get("error_description", "Token refresh failed")}, status_code=400)
        _ebay_token = token
        sess = get_session(session_id)
        if sess:
            sess["ebay_token"] = token
        h = {"Authorization": f"Bearer {token}"}
        b = "https://api.ebay.com/sell/account/v1"
        async with httpx.AsyncClient() as client:
            ship_r, ret_r, pay_r = await asyncio.gather(
                client.get(f"{b}/fulfillment_policy?marketplace_id=EBAY_US", headers=h, timeout=15),
                client.get(f"{b}/return_policy?marketplace_id=EBAY_US", headers=h, timeout=15),
                client.get(f"{b}/payment_policy?marketplace_id=EBAY_US", headers=h, timeout=15),
            )
        ship = ship_r.json()
        ret = ret_r.json()
        pay = pay_r.json()
        return JSONResponse({
            "connected": True,
            "access_token": token,
            "shipping_policies": [{"name": p["name"], "id": p["fulfillmentPolicyId"]} for p in ship.get("fulfillmentPolicies", [])],
            "return_policies": [{"name": p["name"], "id": p["returnPolicyId"]} for p in ret.get("returnPolicies", [])],
            "payment_policies": [{"name": p["name"], "id": p["paymentPolicyId"]} for p in pay.get("paymentPolicies", [])],
        })
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Remove background ──────────────────────────────────────────────────────

def _shrink_for_upload(img_data):
    """Resize image to max 512px and re-encode as JPEG for fast upload."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(img_data))
    img = ImageOps.exif_transpose(img) or img
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > 512:
        s = 512 / max(w, h)
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()

async def _remove_bg_fal(client, img_data):
    """Remove background via fal.ai BiRefNet (cloud GPU)."""
    from PIL import Image
    small = await asyncio.get_event_loop().run_in_executor(None, _shrink_for_upload, img_data)
    b64_input = base64.b64encode(small).decode()
    data_uri = f"data:image/jpeg;base64,{b64_input}"
    resp = await client.post(
        "https://fal.run/fal-ai/birefnet/v2",
        headers={"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"},
        json={"image_url": data_uri},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    image_url = result.get("image", {}).get("url", "")
    if not image_url:
        raise ValueError("fal.ai returned no image")
    img_resp = await client.get(image_url, timeout=15)
    img_resp.raise_for_status()
    removed = Image.open(io.BytesIO(img_resp.content))
    if removed.mode != "RGBA":
        removed = removed.convert("RGBA")
    white = composite_on_white(removed)
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    white.save(out, "JPEG", quality=90); out.close()
    buf = io.BytesIO()
    white.save(buf, "JPEG", quality=90)
    return {"b64": base64.b64encode(buf.getvalue()).decode(), "path": out.name}

@fapp.post("/remove-bg")
async def remove_bg_api(images: List[UploadFile] = File(...)):
    global _last_data
    if not images:
        return JSONResponse({"error": "No images provided"}, status_code=400)
    if not FAL_KEY:
        return JSONResponse({"error": "Background removal not configured (FAL_KEY missing)"}, status_code=503)
    try:
        img_datas = [await f.read() for f in images[:12]]
        async with httpx.AsyncClient(timeout=45) as client:
            tasks = [_remove_bg_fal(client, d) for d in img_datas]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        # Filter out failures, keep successes
        good = [r for r in results if isinstance(r, dict)]
        if not good:
            return JSONResponse({"error": f"Background removal failed: {results[0]}"}, status_code=500)
        new_paths = [r["path"] for r in good]
        if _last_data and "images" in _last_data:
            old_paths = _last_data["images"]
            _last_data["images"] = new_paths
            for p in old_paths:
                try: os.unlink(p)
                except: pass
        else:
            _last_data = {"data": _last_data.get("data", {}), "images": new_paths}
        _metrics["bg_removals"] += 1
        return JSONResponse({"success": True, "images": [r["b64"] for r in results]})
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ── Analyze garment ─────────────────────────────────────────────────────────
@fapp.post("/analyze")
async def analyze_api(images: List[UploadFile] = File(...), gender: str = Form("")):
    global _last_data
    _last_data = {}
    if not images:
        return JSONResponse({"error": "No images"}, status_code=400)
    paths = []
    try:
        for img in images[:12]:
            data = await img.read()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp.write(data); tmp.close()
            fix_orientation(tmp.name)
            paths.append(tmp.name)
        gender_hint = f" The user has indicated this is a {gender} garment." if gender else ""
        # Send up to 8 images to Claude — need enough to include tag/label close-ups
        analyze_paths = paths[:8]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            b64_images = list(pool.map(encode_b64, analyze_paths))
        content = [{"type": "text", "text": f"Analyze these garment photos. CRITICAL: Zoom in on and carefully read ALL visible tags, labels, and printed text — brand labels, size tags, care/content labels, style number tags, RN number tags. Transcribe the EXACT text from each tag. Look at every image for tags.{gender_hint} Return ONLY valid JSON."}]
        for b64 in b64_images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
        resp = claude.messages.create(model=CLAUDE_MODEL, max_tokens=1500, system=SYSTEM_PROMPT,
                                      messages=[{"role": "user", "content": content}])
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        # Create session
        sid = create_session()
        sess = get_session(sid)
        sess["data"] = result
        sess["images"] = paths
        # Also keep global fallback
        _last_data = {"data": result, "images": paths}
        _metrics["analyses"] += 1
        return JSONResponse({"success": True, "data": result, "session_id": sid})
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"success": False, "error": str(e), "trace": traceback.format_exc()[:400]}, status_code=500)

# ── eBay sold comps (Finding API) ──────────────────────────────────────────
async def _fetch_finding_api(query, operation="findCompletedItems", sold_only=True, limit=12):
    params = {
        "OPERATION-NAME": operation,
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": query,
        "paginationInput.entriesPerPage": str(limit),
    }
    filter_idx = 0
    if sold_only and operation == "findCompletedItems":
        params[f"itemFilter({filter_idx}).name"] = "SoldItemsOnly"
        params[f"itemFilter({filter_idx}).value"] = "true"
        filter_idx += 1
    params[f"itemFilter({filter_idx}).name"] = "Condition"
    params[f"itemFilter({filter_idx}).value"] = "3000"
    filter_idx += 1
    params["sortOrder"] = "EndTimeSoonest" if operation == "findCompletedItems" else "BestMatch"
    async with httpx.AsyncClient() as client:
        resp = await client.get(FINDING_API_URL, params=params, timeout=15)
    data = resp.json()
    response_key = f"{operation}Response"
    results = (data.get(response_key, [{}])[0]
                  .get("searchResult", [{}])[0]
                  .get("item", []))
    return results

@fapp.post("/comps")
async def comps_api(req: Request):
    body = await req.json()
    query = body.get("query", "")
    if not query:
        return JSONResponse({"comps": [], "avg_price": 0, "count": 0})
    try:
        items = await _fetch_finding_api(query, "findCompletedItems", sold_only=True, limit=12)
        comps = []
        for item in items[:12]:
            price_info = item.get("sellingStatus", [{}])[0]
            comps.append({
                "title": item.get("title", [""])[0],
                "price": price_info.get("currentPrice", [{}])[0].get("__value__", ""),
                "sold_date": item.get("listingInfo", [{}])[0].get("endTime", [""])[0],
                "condition": item.get("condition", [{}])[0].get("conditionDisplayName", [""])[0] if item.get("condition") else "",
                "image": item.get("galleryURL", [""])[0],
                "url": item.get("viewItemURL", [""])[0],
            })
        prices = [float(c["price"]) for c in comps if c["price"]]
        avg = sum(prices) / len(prices) if prices else 0
        return JSONResponse({"comps": comps, "avg_price": round(avg, 2), "count": len(comps)})
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"comps": [], "avg_price": 0, "count": 0, "error": str(e)})

# ── Sell-through intelligence ───────────────────────────────────────────────
@fapp.post("/sold-history")
async def sold_history_api(req: Request):
    body = await req.json()
    query = body.get("query", "")
    if not query:
        return JSONResponse({"error": "No query provided"}, status_code=400)
    try:
        sold_items, active_items = await asyncio.gather(
            _fetch_finding_api(query, "findCompletedItems", sold_only=True, limit=50),
            _fetch_finding_api(query, "findItemsAdvanced", sold_only=False, limit=50),
        )
        sold_prices = []
        days_list = []
        for item in sold_items:
            price_info = item.get("sellingStatus", [{}])[0]
            p = price_info.get("currentPrice", [{}])[0].get("__value__", "")
            if p:
                sold_prices.append(float(p))
            start = item.get("listingInfo", [{}])[0].get("startTime", [""])[0]
            end = item.get("listingInfo", [{}])[0].get("endTime", [""])[0]
            if start and end:
                try:
                    s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    days_list.append((e - s).days)
                except: pass
        sold_count = len(sold_prices)
        active_count = len(active_items)
        total = sold_count + active_count
        sell_through = round(sold_count / total, 2) if total > 0 else 0
        avg_price = round(sum(sold_prices) / len(sold_prices), 2) if sold_prices else 0
        avg_days = round(sum(days_list) / len(days_list), 1) if days_list else 0
        low = round(min(sold_prices), 2) if sold_prices else 0
        high = round(max(sold_prices), 2) if sold_prices else 0
        return JSONResponse({
            "sold_count": sold_count,
            "active_count": active_count,
            "sell_through_rate": sell_through,
            "avg_sold_price": avg_price,
            "price_range": {"low": low, "high": high},
            "avg_days_to_sell": avg_days,
        })
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Price recommendation engine ─────────────────────────────────────────────
@fapp.post("/price-recommend")
async def price_recommend(req: Request):
    body = await req.json()
    query = body.get("query", "")
    if not query:
        return JSONResponse({"error": "No query provided"}, status_code=400)
    try:
        items = await _fetch_finding_api(query, "findCompletedItems", sold_only=True, limit=50)
        prices = []
        for item in items:
            price_info = item.get("sellingStatus", [{}])[0]
            p = price_info.get("currentPrice", [{}])[0].get("__value__", "")
            if p:
                prices.append(float(p))
        if not prices:
            return JSONResponse({"error": "No sold data available for pricing"})
        prices.sort()
        n = len(prices)
        return JSONResponse({
            "quick_sell": round(prices[max(0, int(n * 0.25))], 2),
            "market": round(sum(prices) / n, 2),
            "premium": round(prices[min(n - 1, int(n * 0.85))], 2),
            "sample_size": n,
            "price_range": {"low": round(min(prices), 2), "high": round(max(prices), 2)},
        })
    except Exception as e:
        _metrics["errors"] += 1
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Publish to eBay (core logic) ───────────────────────────────────────────
async def upload_pic(path, token, retries=3):
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    for attempt in range(retries):
        try:
            xml = '<?xml version="1.0" encoding="utf-8"?><UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents"><RequesterCredentials><eBayAuthToken>' + token + '</eBayAuthToken></RequesterCredentials><PictureName>apoc2</PictureName></UploadSiteHostedPicturesRequest>'
            h = {
                "X-EBAY-API-IAF-TOKEN": token,
                "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
                "X-EBAY-API-APP-NAME": EBAY_APP_ID,
                "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
                "X-EBAY-API-SITEID": "0",
                "X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
            }
            img_bytes = compress_image(path)
            async with httpx.AsyncClient() as client:
                r = await client.post(EBAY_API_URL, headers=h,
                    files={"XML Payload": ("p.xml", xml.encode(), "text/xml"), "image": ("i.jpg", img_bytes, "image/jpeg")},
                    timeout=30)
            url = ET.fromstring(r.text).find(".//e:FullURL", ns)
            if url is not None and url.text:
                return url.text
        except Exception:
            if attempt < retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
    return None

async def _publish_ebay(body):
    global _last_data, _ebay_token
    session_id = body.get("session_id", "")
    sess = get_session(session_id)
    token = body.get("ebay_token") or (sess["ebay_token"] if sess and sess.get("ebay_token") else None) or _ebay_token
    if not token:
        return {"error": "Not connected to eBay", "success": False}
    images_b64 = body.get("images_b64", [])
    _bulk_paths = []
    if images_b64:
        for b64 in images_b64:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp.write(base64.b64decode(b64)); tmp.close()
            _bulk_paths.append(tmp.name)
        data = dict(body.get("analysis_data", {}))
    else:
        sess_data = sess if sess else None
        if sess_data and sess_data.get("images"):
            data = dict(sess_data.get("data", {}))
        elif _last_data:
            data = dict(_last_data.get("data", {}))
        else:
            return {"error": "No analysis data. Run analysis first.", "success": False}
    # Override fields from body
    if body.get("title"):       data["title"] = body["title"]
    if body.get("description"): data["description"] = body["description"]
    for k in ["brand", "style_name", "gender", "size", "size_type", "color", "color_std", "material",
              "sleeve_length", "neckline", "category", "origin", "pattern", "closure",
              "fit", "occasion", "season", "lining_material", "fabric_type", "theme",
              "collar_style", "cuff_style", "sleeve_type", "rise", "leg_style",
              "jacket_length", "dress_length", "character", "performance_activity",
              "insulation_material", "garment_care", "m_chest", "m_waist", "m_inseam"]:
        if body.get(k):
            data[k] = body[k]
    if "style_details" in body: data["style_details"] = body["style_details"]
    if "accents" in body: data["accents"] = body["accents"]
    if "vintage" in body: data["vintage"] = body["vintage"]
    if "tags_present" in body: data["tags_present"] = body["tags_present"]
    if "graphic_print" in body: data["graphic_print"] = body["graphic_print"]
    if "handmade" in body: data["handmade"] = body["handmade"]
    if body.get("cat_id"): data["cat_id"] = body["cat_id"]
    if body.get("features"): data["features"] = body["features"]
    if body.get("care_instructions"): data["care_instructions"] = body["care_instructions"]
    try:
        paths = _bulk_paths if _bulk_paths else (
            (sess["images"] if sess and sess.get("images") else None) or
            _last_data.get("images", [])
        )
        # Parallel image uploads with retry
        upload_tasks = [upload_pic(str(p), token) for p in paths[:12]]
        upload_results = await asyncio.gather(*upload_tasks)
        pics = [u for u in upload_results if u]
        cid = {1: "1000", 2: "1500", 3: "2750", 4: "3000", 5: "3000"}.get(int(data.get("condition_score", 4)), "3000")
        px = "".join(f"<PictureURL>{u}</PictureURL>" for u in pics[:12])
        title = (data.get("title", "") or "")[:80]
        cat_id = data.get("cat_id") or await suggest_category(title, token) or get_cat_id(data.get("category", ""))
        ship_id = body.get("ship_id", "")
        ret_id = body.get("ret_id", "")
        postal = body.get("postal", "10001")
        duration = body.get("duration", "GTC")
        dispatch = body.get("dispatch", "3")
        listing_format = body.get("listing_format", "FixedPriceItem")
        start_price = body.get("start_price", "")
        buy_now_price = body.get("buy_now_price", "")
        best_offer = body.get("best_offer", False)
        min_offer = body.get("min_offer", "")
        seller_profiles = ""
        if ship_id:
            seller_profiles += f"<SellerShippingProfile><ShippingProfileID>{ship_id}</ShippingProfileID></SellerShippingProfile>"
        if ret_id:
            seller_profiles += f"<SellerReturnProfile><ReturnProfileID>{ret_id}</ReturnProfileID></SellerReturnProfile>"
        price_val = float(start_price) if start_price else float(data.get("suggested_price_low", 9.99))
        bin_xml = ""
        if listing_format == "Chinese" and buy_now_price:
            bin_xml = f"<BuyItNowPrice>{float(buy_now_price):.2f}</BuyItNowPrice>"
        bo_xml = ""
        if best_offer:
            bo_xml = "<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled></BestOfferDetails>"
            if min_offer:
                bo_xml += f"<ListingDetails><MinimumBestOfferPrice>{float(min_offer):.2f}</MinimumBestOfferPrice></ListingDetails>"
        sku = body.get("sku", "")
        sku_xml = f"<SKU>{sku}</SKU>" if sku else ""
        # Dynamic shipping weight
        w_lbs, w_oz = get_shipping_weight(data.get("category", ""))
        call_name = "AddFixedPriceItem" if listing_format == "FixedPriceItem" else "AddItem"
        req_tag = "AddFixedPriceItemRequest" if listing_format == "FixedPriceItem" else "AddItemRequest"
        x = f'''<?xml version="1.0" encoding="utf-8"?>
<{req_tag} xmlns="urn:ebay:apis:eBLBaseComponents">
<RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
<Item>
  <Title>{title}</Title>
  <Description><![CDATA[{build_description_html(data, body)}]]></Description>
  <Quantity>1</Quantity>
  <PrimaryCategory><CategoryID>{cat_id}</CategoryID></PrimaryCategory>
  <StartPrice>{price_val:.2f}</StartPrice>
  {bin_xml}
  {sku_xml}
  <ConditionID>{cid}</ConditionID>
  <CategoryMappingAllowed>true</CategoryMappingAllowed>
  <ListingDuration>{duration}</ListingDuration>
  <ListingType>{listing_format}</ListingType>
  <Location>United States</Location><PostalCode>{postal}</PostalCode>
  <Country>US</Country><Currency>USD</Currency><DispatchTimeMax>{dispatch}</DispatchTimeMax>
  <ShippingPackageDetails>
    <WeightMajor unit="lbs">{w_lbs}</WeightMajor><WeightMinor unit="oz">{w_oz}</WeightMinor>
    <PackageDepth unit="inches">3</PackageDepth><PackageLength unit="inches">12</PackageLength>
    <PackageWidth unit="inches">10</PackageWidth><ShippingPackage>PackageThickEnvelope</ShippingPackage>
  </ShippingPackageDetails>
  <PictureDetails>{px}</PictureDetails>
  <ItemSpecifics>{build_specifics(data)}</ItemSpecifics>
  <SellerProfiles>{seller_profiles}</SellerProfiles>
  {bo_xml}
  <Site>US</Site>
</Item></{req_tag}>'''
        h = {
            "X-EBAY-API-IAF-TOKEN": token,
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
            "X-EBAY-API-APP-NAME": EBAY_APP_ID,
            "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-CALL-NAME": call_name,
            "Content-Type": "text/xml",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(EBAY_API_URL, content=x.encode(), headers=h, timeout=30)
        root = ET.fromstring(r.text)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.find(".//e:Ack", ns)
        if ack is not None and ack.text in ("Success", "Warning"):
            iid = root.find(".//e:ItemID", ns)
            item_id = iid.text if iid is not None else "?"
            for p in _bulk_paths:
                try: os.unlink(p)
                except: pass
            _metrics["listings"] += 1
            return {"success": True, "item_id": item_id, "url": f"https://www.ebay.com/itm/{item_id}"}
        errors = "\n".join(e.text for e in root.findall(".//e:Errors/e:LongMessage", ns) if e.text)
        for p in _bulk_paths:
            try: os.unlink(p)
            except: pass
        return {"success": False, "error": errors or "Unknown eBay error"}
    except Exception as e:
        for p in _bulk_paths:
            try: os.unlink(p)
            except: pass
        _metrics["errors"] += 1
        return {"success": False, "error": str(e)}

@fapp.post("/publish")
async def publish_api(req: Request):
    body = await req.json()
    result = await _publish_ebay(body)
    status = 200 if result.get("success") else (401 if "Not connected" in result.get("error", "") else 400)
    return JSONResponse(result, status_code=status)

# ── Multi-platform listing ──────────────────────────────────────────────────
def _platform_data(body):
    """Extract common listing data from body (uses analysis_data or top-level fields)."""
    d = body.get("analysis_data", body)
    title = body.get("title") or d.get("title", "")
    desc = body.get("description") or d.get("description", "")
    brand = body.get("brand") or d.get("brand", "")
    cat = body.get("category") or d.get("category", "")
    size = body.get("size") or d.get("size", "")
    color = body.get("color") or d.get("color", "")
    material = body.get("material") or d.get("material", "")
    condition = d.get("condition_label", "Good")
    price = float(body.get("start_price") or d.get("suggested_price_low", 0) or 0)
    style_name = d.get("style_name", "")
    tags = [t for t in [brand, cat, color, material, size, style_name] if t and t.lower() not in ("none", "null", "")]
    return {"title": title, "desc": desc, "brand": brand, "category": cat, "size": size,
            "color": color, "material": material, "condition": condition, "price": price,
            "style_name": style_name, "tags": tags}

def _format_poshmark(body):
    d = _platform_data(body)
    # Poshmark: 80 char title, 20% seller fee, hashtag description
    posh_price = round(d["price"] * 1.25, 2) if d["price"] else 0  # pad for 20% fee
    hashtags = " ".join(f"#{t.replace(' ', '')}" for t in d["tags"][:6])
    posh_desc = f"{d['desc']}\n\n{hashtags}"
    return {
        "title": d["title"][:80],
        "description": posh_desc,
        "brand": d["brand"],
        "category": d["category"],
        "size": d["size"],
        "color": d["color"],
        "condition": d["condition"],
        "price": posh_price,
        "hashtags": hashtags,
    }

def _format_mercari(body):
    d = _platform_data(body)
    # Mercari: 80 char title, 10% seller fee
    merc_price = round(d["price"] * 1.12, 2) if d["price"] else 0
    cond_map = {"New with Tags": "New", "New without Tags": "Like New", "Excellent": "Like New",
                "Very Good": "Good", "Good": "Good", "Fair": "Fair", "Poor": "Fair"}
    merc_cond = cond_map.get(d["condition"], "Good")
    return {
        "title": d["title"][:80],
        "description": d["desc"],
        "brand": d["brand"],
        "category": d["category"],
        "size": d["size"],
        "color": d["color"],
        "condition": merc_cond,
        "price": merc_price,
    }

def _format_depop(body):
    d = _platform_data(body)
    hashtags = " ".join(f"#{t.replace(' ', '').lower()}" for t in d["tags"][:5])
    depop_desc = f"{d['desc']}\n\n{hashtags}"
    return {
        "title": d["title"][:50],
        "description": depop_desc,
        "brand": d["brand"],
        "category": d["category"],
        "size": d["size"],
        "color": d["color"],
        "condition": d["condition"],
        "price": round(d["price"], 2),
        "hashtags": hashtags,
    }

def _format_facebook(body):
    d = _platform_data(body)
    style = f" {d['style_name']}" if d["style_name"] else ""
    fb_title = f"{d['brand']}{style} — {d['size']}" if d["brand"] else d["title"]
    fb_desc = f"{d['desc']}\n\nBrand: {d['brand']}\nSize: {d['size']}\nColor: {d['color']}\nCondition: {d['condition']}"
    return {
        "title": fb_title[:99],
        "description": fb_desc,
        "brand": d["brand"],
        "category": d["category"],
        "size": d["size"],
        "color": d["color"],
        "condition": d["condition"],
        "price": round(d["price"] * 0.95, 2),  # slightly lower for local/quick sale
    }

PLATFORM_FORMATTERS = {
    "poshmark": _format_poshmark,
    "mercari": _format_mercari,
    "depop": _format_depop,
    "facebook": _format_facebook,
}

@fapp.post("/publish-multi")
async def publish_multi(req: Request):
    body = await req.json()
    platforms = body.get("platforms", ["ebay"])
    results = {}
    if "ebay" in platforms:
        results["ebay"] = await _publish_ebay(body)
    for plat in ["poshmark", "mercari", "depop", "facebook"]:
        if plat in platforms:
            results[plat] = {"status": "manual", "formatted_data": PLATFORM_FORMATTERS[plat](body)}
    return JSONResponse(results)

# ── Delist from eBay ──────────────────────────────────────────────────────
@fapp.post("/delist")
async def delist_api(req: Request):
    body = await req.json()
    item_id = body.get("item_id")
    token = body.get("ebay_token") or _ebay_token
    if not item_id:
        return JSONResponse({"error": "No item_id provided", "success": False}, status_code=400)
    if not token:
        return JSONResponse({"error": "Not connected to eBay", "success": False}, status_code=401)
    try:
        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<EndItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
<RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
<ItemID>{item_id}</ItemID>
<EndingReason>NotAvailable</EndingReason>
</EndItemRequest>'''
        h = {
            "X-EBAY-API-IAF-TOKEN": token,
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
            "X-EBAY-API-APP-NAME": EBAY_APP_ID,
            "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-CALL-NAME": "EndItem",
            "Content-Type": "text/xml",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(EBAY_API_URL, content=xml.encode(), headers=h, timeout=15)
        root = ET.fromstring(r.text)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.find(".//e:Ack", ns)
        if ack is not None and ack.text in ("Success", "Warning"):
            return JSONResponse({"success": True, "item_id": item_id})
        errors = "\n".join(e.text for e in root.findall(".//e:Errors/e:LongMessage", ns) if e.text)
        return JSONResponse({"success": False, "error": errors or "Unknown eBay error"}, status_code=400)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ── Batch listing queue ─────────────────────────────────────────────────────
QUEUE_FILE = "/tmp/apoc2_queue.json"

def _load_queue():
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def _save_queue(queue):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f)

@fapp.post("/queue/add")
async def queue_add(req: Request):
    body = await req.json()
    queue = _load_queue()
    item = {
        "id": str(uuid.uuid4()),
        "status": "pending",
        "data": body,
        "added": datetime.utcnow().isoformat(),
    }
    queue.append(item)
    _save_queue(queue)
    return JSONResponse({"queued": True, "id": item["id"], "position": len(queue)})

@fapp.get("/queue")
async def queue_list():
    return JSONResponse({"items": _load_queue()})

@fapp.post("/queue/process")
async def queue_process(req: Request):
    body = await req.json()
    queue = _load_queue()
    results = []
    for item in queue:
        if item["status"] == "pending":
            result = await _publish_ebay({**item["data"], **body})
            item["status"] = "completed" if result.get("success") else "failed"
            item["result"] = result
            results.append(item)
    _save_queue(queue)
    return JSONResponse({"processed": len(results), "results": results})

@fapp.delete("/queue/{item_id}")
async def queue_remove(item_id: str):
    queue = [i for i in _load_queue() if i["id"] != item_id]
    _save_queue(queue)
    return JSONResponse({"removed": True})

# ── Image utilities ─────────────────────────────────────────────────────────
def fix_orientation(path):
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as img:
            fixed = ImageOps.exif_transpose(img)
            if fixed is not img:
                if fixed.mode == "RGBA":
                    fixed = composite_on_white(fixed)
                elif fixed.mode != "RGB":
                    fixed = fixed.convert("RGB")
                fixed.save(path, "JPEG", quality=92)
    except Exception:
        pass

def composite_on_white(img):
    from PIL import Image
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    return bg

def compress_image(path, max_dim=800, quality=75):
    from PIL import Image
    with Image.open(path) as img:
        if img.mode == "RGBA":
            img = composite_on_white(img)
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            s = max_dim / max(w, h)
            img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue()

def encode_b64(path, max_dim=1400, quality=85):
    """Encode image for Claude analysis — higher res than eBay uploads for tag/label OCR."""
    return base64.standard_b64encode(compress_image(path, max_dim=max_dim, quality=quality)).decode()

# ── HTML description builder ────────────────────────────────────────────────
def build_description_html(data, body):
    import html as h
    def esc(v): return h.escape(str(v)) if v else ""
    title = esc(data.get("title"))
    brand = esc(data.get("brand")) or "Unknown"
    style_name = esc(data.get("style_name"))
    desc = esc(data.get("description"))
    material = esc(data.get("material"))
    color = esc(data.get("color"))
    size = esc(data.get("size"))
    fit = esc(data.get("fit"))
    cond_label = esc(data.get("condition_label")) or "Good"
    cond_notes = esc(data.get("condition_notes"))
    defects = data.get("defects_detected") or []
    features = data.get("features") or []
    care = esc(data.get("care_instructions") or body.get("care_instructions"))
    origin = esc(data.get("origin"))
    pattern = esc(data.get("pattern"))
    sleeve = esc(data.get("sleeve_length"))
    neckline = esc(data.get("neckline"))
    closure = esc(data.get("closure"))
    season = esc(data.get("season"))
    lining = esc(data.get("lining_material"))
    m_chest = esc(body.get("m_chest"))
    m_length = esc(body.get("m_length"))
    m_sleeve = esc(body.get("m_sleeve"))
    m_waist = esc(body.get("m_waist"))
    m_inseam = esc(body.get("m_inseam"))
    m_shoulder = esc(body.get("m_shoulder"))
    has_measurements = any([m_chest, m_length, m_sleeve, m_waist, m_inseam, m_shoulder])
    features_html = ""
    if features:
        items = "".join(f"<li>{h.escape(str(f))}</li>" for f in features[:8])
        features_html = f'<h3 style="font-size:15px;color:#1a1a1a;margin:18px 0 8px;border-bottom:1px solid #e0e0e0;padding-bottom:6px">Design Features</h3><ul style="margin:0 0 12px;padding-left:20px;color:#444;font-size:13px;line-height:1.8">{items}</ul>'
    measurements_html = ""
    if has_measurements:
        rows = ""
        for label, val in [("Chest (pit to pit)", m_chest), ("Length", m_length), ("Sleeve", m_sleeve), ("Shoulder", m_shoulder), ("Waist", m_waist), ("Inseam", m_inseam)]:
            if val:
                rows += f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:600;color:#555;width:50%">{label}</td><td style="padding:6px 12px;border-bottom:1px solid #eee;color:#333">{val}"</td></tr>'
        measurements_html = f'<h3 style="font-size:15px;color:#1a1a1a;margin:18px 0 8px;border-bottom:1px solid #e0e0e0;padding-bottom:6px">Measurements (approx.)</h3><table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px">{rows}</table>'
    detail_rows = ""
    for label, val in [("Brand", brand), ("Style/Model", style_name), ("Size", size), ("Color", color), ("Material", material), ("Pattern", pattern), ("Fit", fit), ("Sleeve Length", sleeve), ("Neckline", neckline), ("Closure", closure), ("Season", season), ("Lining", lining), ("Made in", origin)]:
        if val and val.lower() not in ("none", "null", ""):
            detail_rows += f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:600;color:#555;width:40%">{label}</td><td style="padding:6px 12px;border-bottom:1px solid #eee;color:#333">{val}</td></tr>'
    defect_text = ""
    if defects:
        defect_text = f'<p style="font-size:13px;color:#b45309;margin:6px 0 0">Noted: {h.escape(", ".join(str(d) for d in defects))}</p>'
    condition_html = f'<h3 style="font-size:15px;color:#1a1a1a;margin:18px 0 8px;border-bottom:1px solid #e0e0e0;padding-bottom:6px">Condition: {cond_label}</h3><p style="font-size:13px;color:#444;line-height:1.7;margin:0">{cond_notes}</p>{defect_text}'
    care_html = ""
    if care:
        care_html = f'<p style="font-size:12px;color:#666;margin:12px 0 0"><strong>Care:</strong> {care}</p>'
    return f'''<div style="font-family:Arial,Helvetica,sans-serif;max-width:780px;margin:0 auto;color:#333;line-height:1.6">
<h2 style="font-size:18px;color:#111;margin:0 0 12px;font-weight:700">{title}</h2>
<p style="font-size:14px;color:#444;line-height:1.75;margin:0 0 16px">{desc}</p>
<h3 style="font-size:15px;color:#1a1a1a;margin:18px 0 8px;border-bottom:1px solid #e0e0e0;padding-bottom:6px">Item Details</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:4px">{detail_rows}</table>
{features_html}
{measurements_html}
{condition_html}
{care_html}
</div>'''

# ── eBay listing helpers ────────────────────────────────────────────────────
def get_cat_id(s):
    key = (s or "").lower().strip()
    if key in CATEGORY_MAP:
        return CATEGORY_MAP[key]
    parts = key.split(">")
    for l in range(len(parts) - 1, 0, -1):
        k = " > ".join(p.strip() for p in parts[:l])
        if k in CATEGORY_MAP:
            return CATEGORY_MAP[k]
    return CATEGORY_MAP["default"]

async def suggest_category(title, token):
    if not title or not token:
        return None
    try:
        xml = f'<?xml version="1.0" encoding="utf-8"?><GetSuggestedCategoriesRequest xmlns="urn:ebay:apis:eBLBaseComponents"><RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials><Query>{title[:350]}</Query></GetSuggestedCategoriesRequest>'
        h = {
            "X-EBAY-API-IAF-TOKEN": token,
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
            "X-EBAY-API-APP-NAME": EBAY_APP_ID,
            "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-CALL-NAME": "GetSuggestedCategories",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(EBAY_API_URL, content=xml.encode(), headers=h, timeout=15)
        root = ET.fromstring(r.text)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        cat = root.find(".//e:SuggestedCategoryArray/e:SuggestedCategory/e:Category/e:CategoryID", ns)
        if cat is not None and cat.text:
            return cat.text
    except:
        pass
    return None

def build_specifics(data):
    gender = data.get("gender", "Unisex")
    cat = data.get("category", "")
    kw = " ".join((data.get("style_details", []) or []) + ([cat] if cat else [])).lower()
    vintage = data.get("vintage", False)
    material = (data.get("material") or "").strip()
    color = (data.get("color") or "").strip()
    size = (data.get("size") or "").strip()
    brand = (data.get("brand") or "Unknown").strip()
    dept = {"Men": "Men", "Women": "Women", "Boys": "Boys", "Girls": "Girls"}.get(gender, "Unisex")
    gtype = cat.split(">")[-1].strip() if ">" in cat else (cat or "Clothing")
    sk = kw + " " + " ".join(data.get("style_details", []) or [])
    style = "Vintage" if vintage or any(w in sk for w in ["vintage", "retro", "70s", "80s", "90s"]) else (
        "Activewear" if any(w in sk for w in ["athletic", "sport", "activewear", "gym"]) else (
        "Business" if any(w in sk for w in ["formal", "suit", "blazer"]) else "Casual"))
    mm = {"cotton": "Cotton", "denim": "Denim", "wool": "Wool", "leather": "Leather", "polyester": "Polyester",
          "nylon": "Nylon", "silk": "Silk", "linen": "Linen", "cashmere": "Cashmere", "fleece": "Fleece",
          "velvet": "Velvet", "corduroy": "Corduroy", "tweed": "Tweed", "flannel": "Flannel", "canvas": "Canvas",
          "suede": "Suede", "rayon": "Rayon", "acrylic": "Acrylic", "viscose": "Viscose"}
    om = next((v for k, v in mm.items() if k in material.lower()), material if material else "Cotton")
    cm = {"black": "Black", "white": "White", "gray": "Gray", "grey": "Gray", "navy": "Navy", "blue": "Blue",
          "red": "Red", "green": "Green", "brown": "Brown", "beige": "Beige", "cream": "Cream", "pink": "Pink",
          "purple": "Purple", "multicolor": "Multicolor", "multi": "Multicolor"}
    ec = next((v for k, v in cm.items() if k in color.lower()), color or "Black")
    sl2 = size.lower()
    st = "Plus" if any(x in sl2 for x in ["xxl", "2xl", "3xl", "4xl", "plus"]) else "Regular"
    sleeve = data.get("sleeve_length", "")
    if sleeve not in ["Long Sleeve", "Short Sleeve", "3/4 Sleeve", "Sleeveless", "Cap Sleeve"]:
        sleeve = "Long Sleeve"
    neckline = data.get("neckline", "")
    if neckline not in ["Crew Neck", "V-Neck", "Round Neck", "Scoop Neck", "Turtleneck", "Mock Neck", "Collared",
                        "Hooded", "Polo", "Henley", "Boat Neck", "Cowl Neck", "Square Neck", "Off Shoulder", "Strapless"]:
        neckline = ""
    pattern = data.get("pattern", "")
    closure = data.get("closure", "")
    fit = data.get("fit", "")
    occasion = data.get("occasion", "")
    season = data.get("season", "")
    lining = data.get("lining_material", "")
    specs = [("Brand", brand), ("Size", size or "See Description"), ("Color", ec), ("Department", dept),
             ("Type", gtype), ("Style", style), ("Sleeve Length", sleeve), ("Outer Shell Material", om),
             ("Size Type", st), ("Vintage", "Yes" if vintage else "No")]
    if neckline: specs.append(("Neckline", neckline))
    if pattern and pattern not in ("null", None, ""): specs.append(("Pattern", pattern))
    if closure and closure not in ("null", None, "", "None"): specs.append(("Closure", closure))
    if fit and fit not in ("null", None, ""): specs.append(("Fit", fit))
    if occasion and occasion not in ("null", None, ""): specs.append(("Occasion", occasion))
    if season and season not in ("null", None, ""): specs.append(("Season", season))
    if lining and lining not in ("null", None, ""): specs.append(("Lining Material", lining))
    era = data.get("vintage_era")
    if era and era not in ("null", None, ""): specs.append(("Decade", str(era)))
    _opt = [("Fabric Type", "fabric_type"), ("Theme", "theme"), ("Collar Style", "collar_style"),
            ("Cuff Style", "cuff_style"), ("Sleeve Type", "sleeve_type"), ("Rise", "rise"),
            ("Leg Style", "leg_style"), ("Jacket/Coat Length", "jacket_length"),
            ("Dress Length", "dress_length"), ("Character", "character"),
            ("Performance/Activity", "performance_activity"),
            ("Insulation Material", "insulation_material"), ("Garment Care", "garment_care")]
    for name, key in _opt:
        v = data.get(key, "")
        if v and v not in ("null", None, "", "None", False): specs.append((name, str(v)))
    accents = data.get("accents", [])
    if accents and isinstance(accents, list):
        for a in accents:
            if a and a not in ("null", None, ""): specs.append(("Accents", str(a)))
    feats = data.get("features", [])
    if feats and isinstance(feats, list):
        for f in feats:
            if f and f not in ("null", None, ""): specs.append(("Features", str(f)))
    if data.get("graphic_print"): specs.append(("Graphic Print", "Yes"))
    if data.get("handmade"): specs.append(("Handmade", "Yes"))
    m_chest = data.get("m_chest", "")
    m_waist = data.get("m_waist", "")
    m_inseam = data.get("m_inseam", "")
    if m_chest: specs.append(("Chest Size", f"{m_chest} in"))
    if m_waist: specs.append(("Waist Size", f"{m_waist} in"))
    if m_inseam: specs.append(("Inseam", f"{m_inseam} in"))
    origin = data.get("origin", "")
    if origin and origin not in ("null", None, ""): specs.append(("Country/Region of Manufacture", origin))
    return "".join(f"<NameValueList><Name>{n}</Name><Value>{v}</Value></NameValueList>" for n, v in specs if v)

app = fapp
