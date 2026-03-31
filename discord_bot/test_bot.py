import discord
from discord.ext import commands

import os
from dotenv import load_dotenv

# 加載 .env
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
env_path = os.path.join(parent_dir, '.env') if os.path.exists(os.path.join(parent_dir, '.env')) else os.path.join(current_dir, '.env')
load_dotenv(env_path)
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# 設定 Bot 權限
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 建立下拉選單 (支援多選)
class FilterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Unreal Engine", description="查看 UE 最新動態", emoji="🔹", value="UE 相關內容..."),
            discord.SelectOption(label="Unity", description="查看 Unity 最新動態", emoji="🔸", value="Unity 相關內容..."),
            discord.SelectOption(label="市場新聞", description="產業趨勢與收購", emoji="📈", value="市場新聞內容...")
        ]
        # min_values=1, max_values=3 代表允許使用者「多選」
        super().__init__(placeholder="請選擇你想看的內容 (可多選)...", min_values=1, max_values=3, options=options)

    # 當使用者點擊選單後，觸發這個回調函數
    async def callback(self, interaction: discord.Interaction):
        # 將使用者勾選的所有內容組合起來
        selected_content = "\n\n".join(self.values)
        
        # ephemeral=True 就是「僅你可見」的魔法，不會干擾頻道其他人！
        await interaction.response.send_message(
            content=f"**這是為你專屬生成的隱藏日報！**\n\n{selected_content}", 
            ephemeral=True
        )

# 將選單包裝進 View 中
class ReportView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(FilterSelect())

# 當有人輸入 !test 時發送主訊息
@bot.command()
async def test(ctx):
    await ctx.send("📢 **【今日頭條】** Sony 宣布 PS5 漲價...\n\n👇 *請從下方選單挑選你想深入閱讀的引擎或分類：*", view=ReportView())

@bot.event
async def on_ready():
    print(f'✅ 測試 Bot 已登入：{bot.user}')

# 執行 Bot
bot.run(DISCORD_TOKEN)