import discord
from discord.ext import commands
import aiohttp
import os
import asyncio
from dotenv import load_dotenv

# --- 1. 環境變數加載 (Environmental Configuration) ---
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, '.env'))

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
UB_TOKEN = os.getenv('UB_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')
TARGET_CHANNEL_ID = os.getenv('TARGET_CHANNEL_ID')
TEST_CHANNEL_ID = os.getenv('TEST_CHANNEL_ID')
OFFICIAL_MENTION = os.getenv('OFFICIAL_MENTION')
TEST_MENTION = os.getenv('TEST_MENTION')

# 視覺資源：Z 幣貼圖
Z_COIN_ICON_URL = "https://cdn.discordapp.com/emojis/1483028967152681010.webp?size=96"

# --- 2. 初始化機器人 ---
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True # 重要：用於獲取全體成員清單以執行全體空投
bot = commands.Bot(command_prefix='!', intents=intents)

# --- 3. 核心邏輯封裝 (Core Dispatcher) ---
async def process_reward(ctx, amount, member, reason, is_test=False):
    """處理單體補給邏輯"""
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{member.id}"
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    data = {"cash": amount}

    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json=data, headers=headers) as response:
            if response.status == 200:
                # 視覺面板設定
                title_prefix = "🧪 [測試模式] " if is_test else "🚁 "
                embed = discord.Embed(
                    title=f"{title_prefix}空投 金幣！",
                    description=f"金幣已成功降落在 {member.mention} 的口袋！",
                    color=0x95a5a6 if is_test else 0xFFD700
                )
                embed.set_thumbnail(url=Z_COIN_ICON_URL)
                embed.add_field(name="空投金額", value=f"💰 {amount} 金幣", inline=True)
                embed.add_field(name="管理員", value=ctx.author.display_name, inline=True)
                embed.add_field(name="空投原因", value=reason, inline=False)
                embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

                # 標註邏輯
                raw_mention = TEST_MENTION if is_test else OFFICIAL_MENTION
                mention_str = ""
                if raw_mention:
                    if raw_mention.lower() == "everyone": mention_str = "@everyone"
                    else: mention_str = f"<@&{raw_mention}>"

                # 頻道發送
                channel_id = TEST_CHANNEL_ID if is_test else TARGET_CHANNEL_ID
                target_channel = bot.get_channel(int(channel_id))

                if target_channel:
                    await target_channel.send(
                        content=f"{mention_str} 🚁 **發現空投補給！**" if mention_str else None,
                        embed=embed
                    )
                    await ctx.send(f"✅ 空投成功！公告已同步至 {target_channel.mention}")
            else:
                await ctx.send(f"❌ API 報錯: {response.status}")

# --- 4. 指令區 (Command Controllers) ---

@bot.command(name='空投')
@commands.has_permissions(administrator=True)
async def official_airdrop(ctx, amount: int, member: discord.Member, *, reason: str = "管理員手動獎勵"):
    """針對單一對象的正式空投"""
    await process_reward(ctx, amount, member, reason, is_test=False)

@bot.command(name='測試空投')
@commands.has_permissions(administrator=True)
async def test_airdrop(ctx, amount: int, member: discord.Member, *, reason: str = "開發者環境測試"):
    """針對單一對象的測試空投 (發送至測試頻道)"""
    await process_reward(ctx, amount, member, reason, is_test=True)

@bot.command(name='多重空投')
@commands.has_permissions(administrator=True)
async def multi_airdrop(ctx, amount: int, members: commands.Greedy[discord.Member], *, reason: str = "活動參與獎勵"):
    """針對多位特定對象的空投，並在同一則訊息中顯示所有接收者"""
    if not members:
        await ctx.send("❓ 格式錯誤：請至少標註一位要發放的成員。範例：`!多重空投 10 @玩家A @玩家B 參加活動`")
        return

    # 管理端狀態提示
    status_msg = await ctx.send(f"⏳ 正在為 {len(members)} 位成員發放各 {amount} 金幣...")

    success_members = []
    fail_members = []
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    data = {"cash": amount}

    # 迴圈處理每位玩家的 API 請求
    async with aiohttp.ClientSession() as session:
        for member in members:
            url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{member.id}"
            async with session.patch(url, json=data, headers=headers) as response:
                if response.status == 200:
                    success_members.append(member.mention)
                else:
                    fail_members.append(member.display_name)
                # 頻率保護：每秒處理 5 人
                await asyncio.sleep(0.2) 

    # 組合成功名單字串 (確保不超過 Discord Embed 欄位 1024 字元的限制)
    if success_members:
        success_str = " ".join(success_members)
        if len(success_str) > 1024:
            success_str = success_str[:1020] + "..."
    else:
        success_str = "無人發放成功"

    # 視覺面板設定
    embed = discord.Embed(
        title="🚁 多重空投 金幣！",
        description="金幣已成功降落至以下玩家的口袋！",
        color=0xFFD700
    )
    embed.set_thumbnail(url=Z_COIN_ICON_URL)
    embed.add_field(name="每人獲得金額", value=f"💰 {amount} 金幣", inline=True)
    embed.add_field(name="管理員", value=ctx.author.display_name, inline=True)
    embed.add_field(name="空投原因", value=reason, inline=False)
    
    # 加入成功接收名單的欄位
    embed.add_field(name=f"成功接收名單 ({len(success_members)}人)", value=success_str, inline=False)

    # 如果有失敗的，另外顯示出來以供管理員除錯
    if fail_members:
        embed.add_field(name="❌ 發放失敗名單", value=", ".join(fail_members), inline=False)

    embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

    # 標註邏輯與頻道發送
    mention_str = ""
    if OFFICIAL_MENTION:
        if OFFICIAL_MENTION.lower() == "everyone": 
            mention_str = "@everyone"
        else: 
            mention_str = f"<@&{OFFICIAL_MENTION}>"

    target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))

    if target_channel:
        await target_channel.send(
            content=f"{mention_str} 🚁 **發現多重空投補給！**" if mention_str else None,
            embed=embed
        )
        await status_msg.edit(content=f"✅ 多重空投處理完畢！成功發放給 {len(success_members)} 位成員，公告已同步至 {target_channel.mention}。")
    else:
        await ctx.send("❌ API 處理完成，但找不到目標頻道發布公告，請確認 TARGET_CHANNEL_ID。")


@bot.command(name='測試多重空投')
@commands.has_permissions(administrator=True)
async def test_multi_airdrop(ctx, amount: int, members: commands.Greedy[discord.Member], *, reason: str = "開發者環境測試"):
    """針對多位特定對象的測試空投 (發送至測試頻道)"""
    if not members:
        await ctx.send("❓ 格式錯誤：請至少標註一位要發放的成員。範例：`!測試多重空投 10 @玩家A @玩家B 測試`")
        return

    status_msg = await ctx.send(f"⏳ [測試模式] 正在為 {len(members)} 位成員發放各 {amount} 金幣...")

    success_members = []
    fail_members = []
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    data = {"cash": amount}

    async with aiohttp.ClientSession() as session:
        for member in members:
            url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{member.id}"
            async with session.patch(url, json=data, headers=headers) as response:
                if response.status == 200:
                    success_members.append(member.mention)
                else:
                    fail_members.append(member.display_name)
                await asyncio.sleep(0.2)

    if success_members:
        success_str = " ".join(success_members)
        if len(success_str) > 1024:
            success_str = success_str[:1020] + "..."
    else:
        success_str = "無人發放成功"

    # 測試版視覺面板設定 (標題與顏色改為灰色系)
    embed = discord.Embed(
        title="🧪 [測試模式] 多重空投 金幣！",
        description="金幣已成功降落至以下玩家的口袋！",
        color=0x95a5a6
    )
    embed.set_thumbnail(url=Z_COIN_ICON_URL)
    embed.add_field(name="每人獲得金額", value=f"💰 {amount} 金幣", inline=True)
    embed.add_field(name="管理員", value=ctx.author.display_name, inline=True)
    embed.add_field(name="空投原因", value=reason, inline=False)
    embed.add_field(name=f"成功接收名單 ({len(success_members)}人)", value=success_str, inline=False)

    if fail_members:
        embed.add_field(name="❌ 發放失敗名單", value=", ".join(fail_members), inline=False)

    embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

    # 標註邏輯切換為測試環境用的 TEST_MENTION
    mention_str = ""
    if TEST_MENTION:
        if TEST_MENTION.lower() == "everyone":
            mention_str = "@everyone"
        else:
            mention_str = f"<@&{TEST_MENTION}>"

    # 頻道強制指定為測試頻道 (TEST_CHANNEL_ID)
    target_channel = bot.get_channel(int(TEST_CHANNEL_ID))

    if target_channel:
        await target_channel.send(
            content=f"{mention_str} 🧪 **[測試] 發現多重空投補給！**" if mention_str else None,
            embed=embed
        )
        await status_msg.edit(content=f"✅ 測試多重空投處理完畢！發放 {len(success_members)} 人，公告已送至測試頻道 {target_channel.mention}。")
    else:
        await ctx.send("❌ 找不到測試頻道，請確認 TEST_CHANNEL_ID。")


@bot.command(name='全體空投')
@commands.has_permissions(administrator=True)
async def global_airdrop(ctx, amount: int, *, reason: str = "慶祝「AI 自動日報系統」重大里程碑！"):
    """對全伺服器成員進行群發空投"""
    # 取得所有人類成員
    members = [m for m in ctx.guild.members if not m.bot]
    total = len(members)
    
    # 管理端狀態提示
    status_msg = await ctx.send(f"⏳ 正在為 {total} 位成員分發「AI 自動日報系統」補給... (預計耗時 {int(total*0.2)} 秒)")

    success_count = 0
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    data = {"cash": amount}

    async with aiohttp.ClientSession() as session:
        for member in members:
            url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{member.id}"
            async with session.patch(url, json=data, headers=headers) as response:
                if response.status == 200: success_count += 1
                # 頻率保護：每秒處理 5 人
                await asyncio.sleep(0.2) 

    # 發送全服公告
    target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))
    if target_channel:
        embed = discord.Embed(
            title="🎊 AI 自動日報系統：100 人達成！", # 校準專案名稱
            description=f"感謝大家支持 **AI 自動日報系統**！全體成員皆已獲得 金幣專屬補給。",
            color=0xFF4500
        )
        embed.set_thumbnail(url=Z_COIN_ICON_URL)
        embed.add_field(name="每人獲得", value=f"💰 {amount} 金幣", inline=True)
        embed.add_field(name="公告原因", value=reason, inline=False)
        embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

        mention_str = "@everyone" if OFFICIAL_MENTION.lower() == "everyone" else f"<@&{OFFICIAL_MENTION}>"
        await target_channel.send(content=f"{mention_str} 🚁 **全域補給已送達！**", embed=embed)
        
        # 在發送端顯示成功人數（僅管理員可見，不公開）
        await status_msg.edit(content=f"✅ 全體空投任務圓滿完成！成功發放對象：{success_count} 位成員。")

# --- 5. 事件與報錯處理 ---

@bot.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.listening, name="空投指令")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f'✅ AI 補給系統 [{bot.user.name}] 已連線，專案：AI 自動日報系統')

@official_airdrop.error
@test_airdrop.error
@global_airdrop.error
@multi_airdrop.error 
@test_multi_airdrop.error 
async def airdrop_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 權限不足：只有系統管理員可以使用此空投權限。")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❓ 格式錯誤：請確認參數是否正確（金額、@對象等）。")

bot.run(DISCORD_TOKEN)