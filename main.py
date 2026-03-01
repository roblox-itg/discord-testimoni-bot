import os
import time
import asyncio
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
TESTIMONI_CHANNEL_ID = int(os.getenv("TESTIMONI_CHANNEL_ID", "0"))
REVIEW_CHANNEL_ID = int(os.getenv("REVIEW_CHANNEL_ID", "0"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))

DB_PATH = "testimoni.db"

INTENTS = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=INTENTS)

def is_admin_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if ADMIN_ROLE_ID and any(r.id == ADMIN_ROLE_ID for r in member.roles):
        return True
    return False

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS testimonials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_tag TEXT NOT NULL,
                rating INTEGER NOT NULL,
                product TEXT NOT NULL,
                message TEXT NOT NULL,
                proof_link TEXT,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        await db.commit()

async def insert_testimonial(user_id: int, user_tag: str, rating: int, product: str, message: str, proof_link: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO testimonials (user_id, user_tag, rating, product, message, proof_link, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """, (user_id, user_tag, rating, product, message, proof_link, int(time.time())))
        await db.commit()
        return cur.lastrowid

async def set_status(testi_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE testimonials SET status=? WHERE id=?", (status, testi_id))
        await db.commit()

async def get_testimonial(testi_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, user_tag, rating, product, message, proof_link, status, created_at FROM testimonials WHERE id=?", (testi_id,))
        row = await cur.fetchone()
        return row

def stars(rating: int) -> str:
    rating = max(1, min(5, rating))
    return "⭐" * rating + "☆" * (5 - rating)

def build_embed_pending(testi_id: int, user_tag: str, rating: int, product: str, message: str, proof_link: str | None):
    emb = discord.Embed(
        title=f"📝 Testimoni Baru (ID: {testi_id})",
        description=message,
        color=discord.Color.orange()
    )
    emb.add_field(name="Member", value=user_tag, inline=True)
    emb.add_field(name="Rating", value=f"{stars(rating)} ({rating}/5)", inline=True)
    emb.add_field(name="Produk/Jasa", value=product, inline=False)
    if proof_link:
        emb.add_field(name="Bukti (Link)", value=proof_link, inline=False)
    emb.set_footer(text="Status: PENDING • Klik Approve/Reject di bawah")
    return emb

def build_embed_public(user_tag: str, rating: int, product: str, message: str, proof_link: str | None):
    emb = discord.Embed(
        title="✅ Testimoni Member",
        description=message,
        color=discord.Color.green()
    )
    emb.add_field(name="Member", value=user_tag, inline=True)
    emb.add_field(name="Rating", value=f"{stars(rating)} ({rating}/5)", inline=True)
    emb.add_field(name="Produk/Jasa", value=product, inline=False)
    if proof_link:
        emb.add_field(name="Bukti (Link)", value=proof_link, inline=False)
    emb.set_footer(text="Terima kasih sudah kirim testimoni 🙌")
    return emb


class TestimoniModal(discord.ui.Modal, title="Form Testimoni"):
    rating = discord.ui.TextInput(label="Rating (1-5)", required=True, max_length=1)
    product = discord.ui.TextInput(label="Produk/Jasa", required=True, max_length=80)
    message = discord.ui.TextInput(label="Isi Testimoni", style=discord.TextStyle.paragraph, required=True, max_length=800)
    proof_link = discord.ui.TextInput(label="Link Bukti (opsional)", required=False, max_length=200)

    def __init__(self, requester: discord.Member):
        super().__init__()
        self.requester = requester

    async def on_submit(self, interaction: discord.Interaction):
        try:
            r = int(str(self.rating.value).strip())
        except:
            return await interaction.response.send_message("Rating harus angka 1-5.", ephemeral=True)

        if r < 1 or r > 5:
            return await interaction.response.send_message("Rating harus antara 1 sampai 5.", ephemeral=True)

        prod = str(self.product.value).strip()
        msg = str(self.message.value).strip()
        proof = str(self.proof_link.value).strip() if self.proof_link.value else None
        if proof == "":
            proof = None

        testi_id = await insert_testimonial(
            user_id=self.requester.id,
            user_tag=str(self.requester),
            rating=r,
            product=prod,
            message=msg,
            proof_link=proof
        )

        review_ch = interaction.guild.get_channel(REVIEW_CHANNEL_ID)
        if not review_ch:
            return await interaction.response.send_message("Channel review belum ditemukan.", ephemeral=True)

        view = ReviewActionView(testi_id=testi_id)
        emb = build_embed_pending(testi_id, str(self.requester), r, prod, msg, proof)
        await review_ch.send(embed=emb, view=view)

        await interaction.response.send_message("Testimoni terkirim & menunggu approval admin.", ephemeral=True)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Kirim Testimoni", style=discord.ButtonStyle.success, custom_id="panel:kirim_testimoni")
    async def kirim_testimoni(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TestimoniModal(requester=interaction.user))


class ReviewActionView(discord.ui.View):
    def __init__(self, testi_id: int):
        super().__init__(timeout=None)
        self.testi_id = testi_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.primary)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_admin_member(interaction.user):
            return await interaction.response.send_message("Khusus admin.", ephemeral=True)

        row = await get_testimonial(self.testi_id)
        if not row:
            return await interaction.response.send_message("Data tidak ditemukan.", ephemeral=True)

        _id, user_id, user_tag, rating, product, message, proof_link, status, created_at = row
        if status != "PENDING":
            return await interaction.response.send_message(f"Sudah {status}.", ephemeral=True)

        await set_status(self.testi_id, "APPROVED")

        testi_ch = interaction.guild.get_channel(TESTIMONI_CHANNEL_ID)
        if testi_ch:
            emb_public = build_embed_public(user_tag, rating, product, message, proof_link)
            await testi_ch.send(embed=emb_public)

        await interaction.response.edit_message(content="APPROVED ✅", embed=None, view=None)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_admin_member(interaction.user):
            return await interaction.response.send_message("Khusus admin.", ephemeral=True)

        await set_status(self.testi_id, "REJECTED")
        await interaction.response.edit_message(content="REJECTED ❌", embed=None, view=None)


@bot.event
async def on_ready():
    await init_db()
    bot.add_view(PanelView())

    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        try:
            await bot.tree.sync(guild=guild)
        except Exception as e:
            print("Sync error:", e)

    print(f"Bot ready as {bot.user}")

@bot.tree.command(name="setup_panel", description="Kirim panel testimoni", guild=discord.Object(id=GUILD_ID))
async def setup_panel(interaction: discord.Interaction):
    emb = discord.Embed(
        title="Panel Testimoni",
        description="Klik tombol di bawah untuk kirim testimoni.",
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=emb, view=PanelView())
    await interaction.response.send_message("Panel terkirim.", ephemeral=True)

def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Isi DISCORD_TOKEN di .env")
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
