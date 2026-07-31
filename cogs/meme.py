"""Meme cog — random memes and image captioning.

Commands: /mama meme, /mama caption.
"""

import io
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("mamafm.meme")

MEME_API = "https://meme-api.com/gimme"
FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # stay under Discord's upload limit


class Meme(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    # ---------- /mama meme ----------

    async def meme(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            async with self.session.get(MEME_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            log.exception("Meme API request failed")
            return await interaction.followup.send("Meme API is down, try again later.")

        embed = discord.Embed(
            title=data.get("title", "meme"),
            url=data.get("postLink"),
            color=discord.Color.orange(),
        )
        embed.set_image(url=data["url"])
        embed.set_footer(text=f"r/{data.get('subreddit', '?')} · 👍 {data.get('ups', 0)}")
        await interaction.followup.send(embed=embed)

    # ---------- /mama caption ----------

    async def caption(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        top_text: str,
        bottom_text: str,
    ) -> None:
        if not (image.content_type or "").startswith("image/"):
            return await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
        if image.size > MAX_IMAGE_BYTES:
            return await interaction.response.send_message("Image too big (max 8 MB).", ephemeral=True)

        await interaction.response.defer()
        raw = await image.read()
        try:
            buf = await self.bot.loop.run_in_executor(None, self._render_caption, raw, top_text, bottom_text)
        except Exception:
            log.exception("Caption rendering failed")
            return await interaction.followup.send("Couldn't process that image.")

        await interaction.followup.send(file=discord.File(buf, filename="caption.png"))

    @staticmethod
    def _render_caption(raw: bytes, top_text: str, bottom_text: str) -> io.BytesIO:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        # Cap dimensions so fonts scale sanely and output stays small.
        img.thumbnail((1600, 1600))
        draw = ImageDraw.Draw(img)

        font_size = max(24, img.width // 10)
        font = None
        for path in FONT_CANDIDATES:
            try:
                font = ImageFont.truetype(path, font_size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default(size=font_size)

        def draw_block(text: str, anchor_top: bool) -> None:
            text = text.upper()
            # Shrink font until the text fits the image width.
            f = font
            while f.size > 16 and draw.textlength(text, font=f) > img.width * 0.95:
                f = f.font_variant(size=f.size - 4)
            x = img.width / 2
            bbox = draw.textbbox((0, 0), text, font=f)
            text_h = bbox[3] - bbox[1]
            y = 10 if anchor_top else img.height - text_h - 20
            draw.text(
                (x, y),
                text,
                font=f,
                fill="white",
                stroke_width=max(2, f.size // 15),
                stroke_fill="black",
                anchor="ma",
            )

        if top_text.strip():
            draw_block(top_text, anchor_top=True)
        if bottom_text.strip():
            draw_block(bottom_text, anchor_top=False)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf


async def setup(bot: commands.Bot) -> None:
    cog = Meme(bot)
    await bot.add_cog(cog)
    mama: app_commands.Group = bot.mama

    @mama.command(name="meme", description="Post a random meme")
    async def meme(interaction: discord.Interaction) -> None:
        await cog.meme(interaction)

    @mama.command(name="caption", description="Overlay top/bottom text on an image")
    @app_commands.describe(
        image="Image to caption",
        top_text="Text at the top (use a space for none)",
        bottom_text="Text at the bottom (use a space for none)",
    )
    async def caption(
        interaction: discord.Interaction,
        image: discord.Attachment,
        top_text: str,
        bottom_text: str,
    ) -> None:
        await cog.caption(interaction, image, top_text, bottom_text)
