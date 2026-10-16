"""Roda no GitHub Actions. Para cada pub/<lote>/job.json sem resultado: publica no Instagram e grava resultados/<lote>.json.

job.json: {"acao": "publicar" | "verificar" | "story" | "reel" (com "video": "reel.mp4", "capa": "capa.jpg"), "legenda": "...", "alts": [...], "imagens": ["01.jpg", ...], "story": "story.jpg" (opcional)}
As imagens já estão neste repositório público; a Meta baixa pelo raw.githubusercontent.com (fixado no commit do push).

Também publica os posts preparados em fila/<post>/ (job.json + imagens) quando existe fila/<post>/publicar, o arquivo
que o botão "Publicar" da página de revisão pede para o Lucas criar no GitHub. Resultado em resultados/fila-<post>.json;
um post que já tem resultado ok nunca é publicado de novo.
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
    # Consulta poucas vezes: cada consulta gasta a cota de pedidos por hora do app ("Application request limit").
    t0 = time.time()
    time.sleep(8)
    while time.time() - t0 < limite:
        st = graph("GET", cid, fields="status_code").get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise Erro(f"O Instagram não conseguiu processar a mídia ({st}).")
        time.sleep(20)
    raise Erro("O Instagram demorou demais para processar as imagens.")


def publicar_container(uid, cid, legenda=None):
    """media_publish; se o Instagram responder erro mas tiver publicado (acontece com o limite de pedidos),
    confirma pelo status do container e acha a publicação na lista do perfil."""
    try:
        return graph("POST", f"{uid}/media_publish", creation_id=cid)["id"]
    except Erro as e:
        time.sleep(30)
        try:
            st = graph("GET", cid, fields="status_code").get("status_code")
        except Erro:
            st = None
        if st != "PUBLISHED":
            raise e
        if legenda is None:
            return "publicado-sem-id"
        for m in graph("GET", f"{uid}/media", fields="id,caption", limit=6).get("data", []):
            if (m.get("caption") or "").strip()[:80] == legenda.strip()[:80]:
                return m["id"]
        return "publicado-sem-id"


def dias_token():
    g = os.environ.get("IG_TOKEN_GERADO_EM")
    try:
        return 60 - (dt.date.today() - dt.date.fromisoformat(g.strip())).days if g else None
    except ValueError:
        return None


def publicar(lote, job, pasta="pub"):
    me = graph("GET", "me", fields="user_id,username")
    uid = me.get("user_id") or me.get("id")
    if job.get("acao") == "recentes":  # o que está no perfil agora (para conferir publicações)
        ms = graph("GET", f"{uid}/media", fields="id,caption,media_type,media_product_type,timestamp,permalink", limit=12).get("data", [])
        try:
            sts = graph("GET", f"{uid}/stories", fields="id,timestamp,permalink").get("data", [])
        except Erro as e:
            sts = [{"erro": str(e)}]
        return {"ok": True, "conta": me.get("username"), "midia": [dict(m, caption=(m.get("caption") or "")[:90]) for m in ms], "stories": sts}
    if job.get("acao") == "verificar":
        lim = graph("GET", f"{uid}/content_publishing_limit", fields="quota_usage,config")
        return {"ok": True, "conta": me.get("username"), "limite": lim.get("data")}
    base = f"https://raw.githubusercontent.com/{REPO}/{SHA}/{pasta}/{lote}"
    if job.get("acao") == "reel":
        extra = {"cover_url": f"{base}/{job['capa']}"} if job.get("capa") else {}
        c = graph("POST", f"{uid}/media", media_type="REELS", video_url=f"{base}/{job['video']}", caption=job["legenda"], share_to_feed="true", **extra)
        esperar(c["id"], limite=900)
        pid = publicar_container(uid, c["id"], job["legenda"])
        info = graph("GET", pid, fields="permalink") if pid != "publicado-sem-id" else {}
        return {"ok": True, "id": pid, "permalink": info.get("permalink"), "conta": me.get("username")}
    if job.get("acao") == "story":
        r = publicar_story(uid, base, job["story"])
        return {"ok": "story_id" in r, **r, **({"erro": r["story_erro"]} if "story_erro" in r else {}), "conta": me.get("username")}
    filhos = [graph("POST", f"{uid}/media", image_url=f"{base}/{img}", is_carousel_item="true", alt_text=job["alts"][i])["id"]
              for i, img in enumerate(job["imagens"])]
    for c in filhos:
        esperar(c)
    extra = {"location_id": os.environ["IG_LOCATION_ID"]} if os.environ.get("IG_LOCATION_ID") else {}
    car = graph("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(filhos), caption=job["legenda"], **extra)
    esperar(car["id"])
    pid = publicar_container(uid, car["id"], job["legenda"])
    info = graph("GET", pid, fields="permalink") if pid != "publicado-sem-id" else {}
    res = {"ok": True, "id": pid, "permalink": info.get("permalink"), "conta": me.get("username")}
    if job.get("story"):  # story publicado logo depois do carrossel; uma falha aqui não desfaz o post
        res.update(publicar_story(uid, base, job["story"]))
    return res


def publicar_story(uid, base, arquivo):
    ultimo = None
    for tentativa in range(3):  # a Meta às vezes responde "resource does not exist" logo após outro publish
        try:
            time.sleep(10 * (tentativa + 1))
            st = graph("POST", f"{uid}/media", image_url=f"{base}/{arquivo}", media_type="STORIES")
            esperar(st["id"])
            time.sleep(5)
            return {"story_id": publicar_container(uid, st["id"])}
        except Erro as e:
            ultimo = str(e)
    return {"story_erro": ultimo}


os.makedirs("resultados", exist_ok=True)


def gravar(nome, res):
    res.update({"em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "dias_para_vencer_token": dias_token()})
    json.dump(res, open(f"resultados/{nome}.json", "w"), ensure_ascii=False, indent=1)
    print(nome, "ok" if res["ok"] else "erro")


# Posts preparados que o Lucas mandou publicar pela página (fila/<post>/publicar).
for gatilho in sorted(glob.glob("fila/*/publicar")):
    post = gatilho.split("/")[1]
    nome = f"fila-{post}"
    anterior = json.load(open(f"resultados/{nome}.json")) if os.path.exists(f"resultados/{nome}.json") else {}
    if anterior.get("ok"):
        os.remove(gatilho)  # já publicado: um segundo clique não publica de novo
        continue
    if not os.path.exists(f"fila/{post}/job.json"):
        res = {"ok": False, "erro": "Este post ainda não foi preparado para publicação (aguarde a próxima execução da esteira e clique de novo)."}
        os.remove(gatilho)
    else:
        job = json.load(open(f"fila/{post}/job.json"))
        try:
            res = publicar(post, job, pasta="fila")
        except Erro as e:
            res = {"ok": False, "erro": str(e)}
        except Exception as e:
            res = {"ok": False, "erro": f"Falha inesperada: {e}"}
        res.update({"post": post, "assets": job.get("assets")})
        if res["ok"]:
            shutil.rmtree(f"fila/{post}", ignore_errors=True)
        else:
            os.remove(gatilho)  # mantém o post preparado para tentar de novo
    gravar(nome, res)

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
    res["lote"] = lote
    gravar(lote, res)
    shutil.rmtree(f"pub/{lote}", ignore_errors=True)  # as imagens só precisam ficar no ar durante a publicação
