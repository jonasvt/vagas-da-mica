"""Bot do Discord — jeito simples de usar o localizador.

Comandos (funcionam na conversa privada com o bot ou num servidor):
    /vagas palavras [sem]   procura agora
    /alerta palavras [sem]  posta no canal (onde foi criado) as vagas novas, sem marcar ninguém
    /alertas                lista seus alertas
    /apagar numero          apaga um alerta
    /ajuda                  explica tudo isso

Encaminhar (ou colar) uma vaga na conversa privada com o bot salva a vaga.
Só entram vagas remotas abertas a quem está no Brasil.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import tasks

from . import busca, config, db
from .util import hours_since, truncate

log = logging.getLogger("vagas")

AJUDA = (
    "**Como usar** 👇\n"
    "• `/vagas python, backend` — procura agora\n"
    "• `/alerta python, cientista de dados` — use **dentro do canal** onde quer receber: as vagas novas aparecem nele\n"
    "• `/alertas` — vê seus alertas · `/apagar 3` — apaga o alerta 3\n"
    "• Use vírgula para separar opções. No campo **sem**, coloque o que não quer no título (ex.: `senior, estágio`).\n"
    "• Viu uma vaga no Discord? **Encaminhe** para mim aqui. No LinkedIn, cole aqui o link do post + o texto.\n"
    "Só mostro vagas **remotas** que aceitam quem está no Brasil."
)
UNIDADE = {"video": "vídeo", "short": "short", "minute": "min", "hour": "hora", "month": "mês", "week": "semana",
           "year": "ano", "project": "projeto", "unknown": ""}


def _quando(iso: str | None) -> str:
    h = hours_since(iso)
    if h is None:
        return "data não informada"
    if h < 1:
        return f"há {max(1, round(h * 60))} min"
    if h < 48:
        return f"há {round(h)} h"
    return f"há {round(h / 24)} dias"


def _valor(j: dict) -> str | None:
    if not j.get("comp_min"):
        return None
    lo, hi = j["comp_min"], j.get("comp_max") or j["comp_min"]
    n = (lambda x: f"{x:,.0f}".replace(",", "."))
    v = n(lo) if lo == hi else f"{n(lo)}–{n(hi)}"
    un = UNIDADE.get(j.get("comp_unit") or "", "")
    return f"{j.get('comp_currency') or ''} {v}{'/' + un if un else ''}".strip()


def job_embed(j: dict) -> discord.Embed:
    url = j.get("source_url") if re.match(r"^https?://", j.get("source_url") or "") else None
    e = discord.Embed(title=truncate(j.get("title") or "Vaga", 250), url=url, color=0x1F7A4D)
    partes = []
    if j.get("company"):
        partes.append(f"🏢 {truncate(j['company'], 60)}")
    if _valor(j):
        partes.append(f"💰 {_valor(j)}")
    partes.append(f"🌎 {truncate(j.get('location_text') or 'Remoto', 60)}")
    partes.append(f"🕒 {_quando(j.get('published_at') or j.get('discovered_at'))}")
    e.description = " · ".join(partes)
    fonte = j.get("community") or j.get("source_name") or ""
    e.set_footer(text=fonte + (" · palavra aparece no texto" if j.get("_onde") == "texto" else ""))
    return e


def _texto_do_snapshot(snap) -> str:
    partes = [snap.content or ""]
    for em in getattr(snap, "embeds", []) or []:
        partes += [em.title or "", em.description or ""]
    return "\n".join(p for p in partes if p).strip()


class BotVagas(discord.Client):
    def __init__(self, conn, cfg: dict):
        # Intents padrão já incluem mensagens privadas; ler DM não exige intent privilegiada.
        super().__init__(intents=discord.Intents.default())
        self.conn, self.cfg = conn, cfg
        self.tree = app_commands.CommandTree(self)
        registrar_comandos(self)

    async def setup_hook(self):
        guild_id = os.environ.get("DISCORD_GUILD_ID")
        if guild_id:  # comandos aparecem na hora nesse servidor
            g = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        await self.tree.sync()
        self.ciclo.start()

    async def on_ready(self):
        log.info("bot conectado como %s", self.user)

    @tasks.loop(seconds=60)
    async def ciclo(self):
        try:
            await asyncio.to_thread(busca.coletar, self.conn, self.cfg)
        except Exception:  # noqa: BLE001 — o ciclo nunca pode morrer
            log.exception("erro na coleta")
        await self.enviar_alertas()

    async def enviar_alertas(self):
        """Posta as vagas novas no canal de cada alerta. Sem mensagem privada e sem marcar ninguém."""
        envios = await asyncio.to_thread(busca.preparar_envios, self.conn)
        maximo = int(self.cfg.get("max_vagas_por_mensagem", 10))
        for canal_id, jobs in envios.items():
            ids = [j["id"] for j in jobs]
            try:
                canal = self.get_channel(int(canal_id)) or await self.fetch_channel(int(canal_id))
                extra = f"\n(+{len(jobs) - maximo} outras — use `/vagas` para ver)" if len(jobs) > maximo else ""
                await canal.send(content=f"🔔 **{len(jobs)} vaga(s) nova(s)**{extra}",
                                 embeds=[job_embed(j) for j in jobs[:maximo]],
                                 allowed_mentions=discord.AllowedMentions.none())
                busca.marcar_enviadas(self.conn, canal_id, ids)
            except discord.NotFound:
                log.warning("canal %s não existe mais; alertas dele desativados", canal_id)
                busca.desativar_canal(self.conn, canal_id)
            except discord.Forbidden:
                log.warning("sem permissão para postar no canal %s (precisa de Enviar mensagens e Inserir links)", canal_id)
                busca.marcar_enviadas(self.conn, canal_id, ids)
            except Exception:  # noqa: BLE001
                log.exception("falha ao postar no canal %s", canal_id)

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return  # só conversa privada
        snaps = getattr(message, "message_snapshots", None) or []
        link, criada = None, None
        if snaps:
            texto = _texto_do_snapshot(snaps[0])
            criada = snaps[0].created_at
            ref = message.reference
            if ref and ref.channel_id and ref.message_id:
                link = f"https://discord.com/channels/{ref.guild_id or '@me'}/{ref.channel_id}/{ref.message_id}"
        elif len(message.content or "") >= 80:
            texto = message.content
            link = busca.link_da_vaga(texto)
        else:
            await message.channel.send(AJUDA)
            return
        res = await asyncio.to_thread(busca.importar, self.conn, texto, link, criada, str(message.author))
        st = res["status"]
        if st == "salva":
            await message.channel.send(f"✅ Guardei: **{truncate(res['job']['title'], 120)}**")
            await self.enviar_alertas()  # posta nos canais cujo alerta combina
        elif st == "repetida":
            await message.channel.send("Essa vaga já estava guardada 👍")
        elif st == "nao_remota" and res.get("modo") == "unknown":
            await message.channel.send("Não guardei: a vaga não diz que é **remota** (no LinkedIn eu só guardo quando está escrito).")
        elif st == "nao_remota":
            await message.channel.send("Essa parece **presencial ou híbrida** — não guardei (só guardo remotas).")
        elif st == "sem_certeza":
            await message.channel.send(f"Não guardei: {res['motivo']}. Post do LinkedIn só entra quando tenho certeza "
                                       "de que é vaga, com contato e remota.")
        elif st == "outro_pais":
            await message.channel.send(f"Essa é remota só para outro país ({res['motivo']}) — não guardei.")
        else:
            await message.channel.send("Não entendi a vaga. Encaminhe a mensagem original ou cole o texto completo.")


def registrar_comandos(bot: BotVagas) -> None:
    tree = bot.tree
    contextos = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)

    @tree.command(name="vagas", description="Procura vagas remotas agora")
    @app_commands.describe(palavras="Ex.: python, backend, cientista de dados", sem="Palavras que NÃO quer no título (opcional)")
    @contextos
    async def vagas(inter: discord.Interaction, palavras: str, sem: str | None = None):
        await inter.response.defer(ephemeral=True, thinking=True)
        await asyncio.to_thread(busca.buscar_online, bot.conn, bot.cfg, busca.separar(palavras))
        jobs, total = await asyncio.to_thread(busca.buscar, bot.conn, palavras, sem,
                                              dias=int(bot.cfg.get("dias_na_busca", 15)),
                                              limite=int(bot.cfg.get("max_vagas_por_mensagem", 10)))
        if not jobs:
            await inter.followup.send("Não achei vaga remota com isso nos últimos dias. Tente outras palavras "
                                      "ou crie um `/alerta` que eu aviso quando aparecer.", ephemeral=True)
            return
        await inter.followup.send(f"**{total}** vaga(s) remota(s) para **{palavras}** — mostrando as {len(jobs)} mais recentes:",
                                  embeds=[job_embed(j) for j in jobs], ephemeral=True)

    @tree.command(name="alerta", description="Posta neste canal as vagas novas com estas palavras")
    @app_commands.describe(palavras="Ex.: python, backend, cientista de dados", sem="Palavras que NÃO quer no título (opcional)")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def alerta(inter: discord.Interaction, palavras: str, sem: str | None = None):
        perm = inter.channel.permissions_for(inter.guild.me)
        if not (perm.send_messages and perm.embed_links and perm.view_channel):
            await inter.response.send_message("Não consigo postar neste canal. Nas permissões do canal, deixe o bot "
                                              "**Ver canal**, **Enviar mensagens** e **Inserir links**.", ephemeral=True)
            return
        try:
            aid = busca.criar_alerta(bot.conn, inter.user.id, palavras, sem, canal_id=inter.channel_id)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        jobs, total = busca.buscar(bot.conn, palavras, sem, dias=7, limite=3)
        msg = (f"✅ Alerta **#{aid}**: as vagas novas com **{palavras}**" + (f" (sem: {sem})" if sem else "") +
               " vão aparecer aqui neste canal.")
        if jobs:
            msg += f"\nJá tem **{total}** da última semana — as mais recentes:"
        await inter.response.send_message(msg, embeds=[job_embed(j) for j in jobs],
                                          allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="alertas", description="Mostra seus alertas")
    @contextos
    async def alertas(inter: discord.Interaction):
        rows = busca.listar_alertas(bot.conn, inter.user.id)
        if not rows:
            await inter.response.send_message("Você ainda não tem alertas. Num canal do servidor, use `/alerta python, backend`.",
                                              ephemeral=True)
            return
        linhas = [f"**#{a['id']}** {a['termos']}" + (f" · sem: {a['excluir']}" if a["excluir"] else "") +
                  (f" → <#{a['canal_id']}>" if a.get("canal_id") else "") for a in rows]
        await inter.response.send_message("Seus alertas:\n" + "\n".join(linhas) + "\nPara apagar: `/apagar numero`", ephemeral=True)

    @tree.command(name="apagar", description="Apaga um alerta")
    @app_commands.describe(numero="O número do alerta (veja em /alertas)")
    @contextos
    async def apagar(inter: discord.Interaction, numero: int):
        ok = busca.apagar_alerta(bot.conn, inter.user.id, numero)
        await inter.response.send_message(f"🗑️ Alerta #{numero} apagado." if ok else "Não achei esse alerta. Veja em `/alertas`.",
                                          ephemeral=True)

    @tree.command(name="ajuda", description="Como usar o bot de vagas")
    @contextos
    async def ajuda(inter: discord.Interaction):
        await inter.response.send_message(AJUDA, ephemeral=True)


def rodar() -> int:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("Falta o DISCORD_TOKEN no arquivo .env — veja o passo a passo no README (seção 'Como ligar').")
        return 1
    conn = db.connect(config.DB_PATH)
    BotVagas(conn, busca.load_config()).run(token, log_handler=None)
    return 0
