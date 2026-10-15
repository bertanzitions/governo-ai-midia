"""Roda no GitHub Actions. Para cada pub/<lote>/job.json sem resultado: publica no Instagram e grava resultados/<lote>.json.

job.json: {"acao": "publicar" | "verificar", "legenda": "...", "alts": [...], "imagens": ["01.jpg", ...]}
As imagens já estão neste repositório público; a Meta baixa pelo raw.githubusercontent.com (fixado no commit do push).
O token vem do segredo IG_TOKEN e nunca é impresso.
"""
import datetime as dt, glob, json, os, shutil, time, urllib.error, urllib.parse, urllib.request

GRAPH = "https://graph.instagram.com"
TOKEN = os.environ.get("IG_TOKEN", "")
REPO, SHA = os.environ["REPO"], os.environ["SHA"]


class Erro(Exception):
    pass


def graph(metodo, caminho, **params):
    if not TOKEN:
        raise Erro("O segredo IG_TOKEN não está configurado no repositório governo-ai-midia (Settings → Secrets and variables → Actions).")
    params["access_token"] = TOKEN
    dados = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{caminho}"
    req = urllib.request.Request(url + "?" + dados.decode()) if metodo == "GET" else urllib.request.Request(url, data=dados, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode(errors="replace")
        try:
            err = json.loads(corpo)["error"]
            msg = err.get("message", corpo)
            if err.get("code") == 190:
                msg = "O token do Instagram venceu ou é inválido: gere um novo na Meta e troque o segredo IG_TOKEN. (" + msg + ")"
        except Exception:
            msg = corpo
        raise Erro(f"Instagram recusou {caminho.split('/')[-1]}: {msg}")


def esperar(cid, limite=240):
    t0 = time.time()
    while time.time() - t0 < limite:
        st = graph("GET", cid, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise Erro(f"O Instagram não conseguiu processar a mídia ({st}).")
        time.sleep(5)
    raise Erro("O Instagram demorou demais para processar as imagens.")


def dias_token():
    g = os.environ.get("IG_TOKEN_GERADO_EM")
    try:
        return 60 - (dt.date.today() - dt.date.fromisoformat(g.strip())).days if g else None
    except ValueError:
        return None


def publicar(lote, job):
    me = graph("GET", "me", fields="user_id,username")
    uid = me.get("user_id") or me.get("id")
    if job.get("acao") == "verificar":
        lim = graph("GET", f"{uid}/content_publishing_limit", fields="quota_usage,config")
        return {"ok": True, "conta": me.get("username"), "limite": lim.get("data")}
    base = f"https://raw.githubusercontent.com/{REPO}/{SHA}/pub/{lote}"
    filhos = [graph("POST", f"{uid}/media", image_url=f"{base}/{img}", is_carousel_item="true", alt_text=job["alts"][i])["id"]
              for i, img in enumerate(job["imagens"])]
    for c in filhos:
        esperar(c)
    extra = {"location_id": os.environ["IG_LOCATION_ID"]} if os.environ.get("IG_LOCATION_ID") else {}
    car = graph("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(filhos), caption=job["legenda"], **extra)
    esperar(car["id"])
    pub = graph("POST", f"{uid}/media_publish", creation_id=car["id"])
    info = graph("GET", pub["id"], fields="permalink")
    return {"ok": True, "id": pub["id"], "permalink": info.get("permalink"), "conta": me.get("username")}


os.makedirs("resultados", exist_ok=True)
for arq in sorted(glob.glob("pub/*/job.json")):
    lote = arq.split("/")[1]
    if os.path.exists(f"resultados/{lote}.json"):
        continue
    job = json.load(open(arq))
    try:
        res = publicar(lote, job)
    except Erro as e:
        res = {"ok": False, "erro": str(e)}
    except Exception as e:
        res = {"ok": False, "erro": f"Falha inesperada: {e}"}
    res.update({"lote": lote, "em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "dias_para_vencer_token": dias_token()})
    json.dump(res, open(f"resultados/{lote}.json", "w"), ensure_ascii=False, indent=1)
    print(lote, "ok" if res["ok"] else "erro")
    shutil.rmtree(f"pub/{lote}", ignore_errors=True)  # as imagens só precisam ficar no ar durante a publicação
