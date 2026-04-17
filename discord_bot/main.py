import discord
from discord.ext import commands
import aiohttp
import os
import asyncio
import sys
import json
import atexit
from dotenv import load_dotenv
import datetime
from discord.ext import tasks

# --- 1. 環境變數加載 (Environmental Configuration) ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

# 同時支援讀取專案根目錄與 discord_bot 下的 .env
env_path = os.path.join(parent_dir, '.env') if os.path.exists(os.path.join(parent_dir, '.env')) else os.path.join(current_dir, '.env')
load_dotenv(env_path)

DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
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

# --- 2.5 共用 API 工具與審計系統 ---

# Money Log 審計目錄
MONEY_LOG_DIR = os.path.join(current_dir, "money_logs")
MONEY_LOG_KEEP_COUNT = 10  # 保留最近 N 筆紀錄（至少 2）

# 全體空投重複執行防護鎖
_global_airdrop_running = False

# --- PID 鎖檔機制：確保單一實例運行 ---
BOT_LOCK_FILE = os.path.join(current_dir, ".bot.pid")


def _is_pid_running(pid):
    """檢查指定 PID 的程序是否仍在運行"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 程序存在但沒有權限瀏覽
    except OSError:
        return False
    return True


def _acquire_bot_lock():
    """嘗試取得 PID 鎖。如果已有其他實例在運行，則拒絕啟動。"""
    if os.path.exists(BOT_LOCK_FILE):
        try:
            with open(BOT_LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if _is_pid_running(old_pid) and old_pid != os.getpid():
                print(f"[BLOCKED] 已偵測到另一個機器人實例正在運行 (PID: {old_pid})！")
                print(f"[BLOCKED] 請先終止舊的實例再啟動，或刪除 {BOT_LOCK_FILE} 後重試。")
                sys.exit(1)
            else:
                print(f"[INFO] 發現過期的鎖檔 (PID: {old_pid} 已不存在)，清除並繼續啟動。")
        except (ValueError, FileNotFoundError):
            pass  # 損壞的鎖檔，忽略並繼續

    # 寫入當前 PID
    with open(BOT_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    print(f"[OK] PID 鎖已取得 (PID: {os.getpid()})")


def _release_bot_lock():
    """釋放 PID 鎖。"""
    try:
        if os.path.exists(BOT_LOCK_FILE):
            with open(BOT_LOCK_FILE, "r") as f:
                stored_pid = int(f.read().strip())
            # 只刪除自己的鎖
            if stored_pid == os.getpid():
                os.remove(BOT_LOCK_FILE)
                print(f"[OK] PID 鎖已釋放")
    except Exception:
        pass


async def _patch_user_balance(session, user_id, data, headers, max_retries=3):
    """帶 429 重試的 UnbelievaBoat PATCH 請求。回傳 (success: bool, status_code: int)"""
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{user_id}"
    for attempt in range(max_retries):
        try:
            async with session.patch(url, json=data, headers=headers) as response:
                if response.status == 200:
                    return True, 200
                elif response.status == 429:
                    retry_after = 1.0
                    try:
                        body = await response.json()
                        retry_after = body.get('retry_after', 1000) / 1000
                    except:
                        pass
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    return False, response.status
        except Exception:
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0)
                continue
            return False, 0
    return False, 429  # 重試耗盡


async def _fetch_all_balances(session, headers):
    """透過 Leaderboard API 分頁取得全伺服器成員餘額快照"""
    all_users = []
    page = 1
    while True:
        url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/?sort=total&limit=100&page={page}"
        try:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    break
                response_data = await response.json()
                users = response_data.get('users', response_data) if isinstance(response_data, dict) else response_data
                if not users:
                    break
                all_users.extend(users)
                if len(users) < 100:
                    break
                page += 1
                await asyncio.sleep(0.3)
        except Exception:
            break
    return all_users


def _save_money_log(log_data):
    """將審計紀錄寫入 money_logs/ 目錄，並清理超額舊檔"""
    os.makedirs(MONEY_LOG_DIR, exist_ok=True)
    tz = datetime.timezone(datetime.timedelta(hours=8))
    timestamp = datetime.datetime.now(tz).strftime("%Y%m%d_%H%M%S")
    command_name = log_data.get("command", "unknown")
    filename = f"{timestamp}_{command_name}.json"
    filepath = os.path.join(MONEY_LOG_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)

    # 清理舊檔：保留最近 N 筆
    try:
        logs = sorted(
            [f for f in os.listdir(MONEY_LOG_DIR) if f.endswith('.json')],
            reverse=True
        )
        for old_file in logs[max(MONEY_LOG_KEEP_COUNT, 2):]:
            os.remove(os.path.join(MONEY_LOG_DIR, old_file))
    except Exception:
        pass

    return filepath


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

                # 標註邏輯：改為只標註成功接收發放的對象
                mention_str = member.mention

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

    # 迴圈處理每位玩家的 API 請求（帶 429 重試）
    async with aiohttp.ClientSession() as session:
        for member in members:
            success, status = await _patch_user_balance(session, member.id, data, headers)
            if success:
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

    # 標註邏輯：改為只標註成功發放對象
    mention_str = " ".join(success_members)
    if len(mention_str) > 1900:
        mention_str = mention_str[:1900] + "..."

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
            success, status = await _patch_user_balance(session, member.id, data, headers)
            if success:
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

    # 標註邏輯：改為只標註測試成功發放對象
    mention_str = " ".join(success_members)
    if len(mention_str) > 1900:
        mention_str = mention_str[:1900] + "..."

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


@bot.command(name='互動發放')
@commands.has_permissions(administrator=True)
async def reaction_airdrop(ctx, message_id: int, amount: int, *, reason: str = "互動參與獎勵"):
    """根據目標訊息的表情互動，自動發放金幣給所有互動過的玩家（去重複）"""

    # Step 1: 讀取歷史訊息
    target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))
    target_message = None

    try:
        if target_channel:
            target_message = await target_channel.fetch_message(message_id)
    except:
        pass

    if not target_message:
        try:
            target_message = await ctx.channel.fetch_message(message_id)
        except:
            pass

    if not target_message:
        await ctx.send("❌ 找不到指定的訊息！請確認 message_id 是否正確。")
        return

    # Step 2: 解析表情與去重複 — 使用 set() 確保同一玩家只記錄一次
    status_msg = await ctx.send("⏳ 正在掃描訊息的表情互動紀錄...")
    unique_users = set()

    for reaction in target_message.reactions:
        async for user in reaction.users():
            if not user.bot:
                unique_users.add(user)

    if not unique_users:
        await status_msg.edit(content="⚠️ 該訊息沒有任何非機器人使用者的表情互動，已取消發放。")
        return

    await status_msg.edit(content=f"⏳ 偵測到 {len(unique_users)} 位互動玩家，正在發放每人 {amount} 金幣...")

    # Step 3: API 串接與頻率限制
    success_members = []
    fail_members = []
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    data = {"cash": amount}

    async with aiohttp.ClientSession() as session:
        for user in unique_users:
            success, status = await _patch_user_balance(session, user.id, data, headers)
            if success:
                success_members.append(user.mention)
            else:
                fail_members.append(user.display_name)
            # 頻率保護：每秒處理 5 人
            await asyncio.sleep(0.2)

    # Step 4: 結果結算與 Embed 面板
    if success_members:
        success_str = " ".join(success_members)
        if len(success_str) > 1024:
            success_str = success_str[:1020] + "..."
    else:
        success_str = "無人發放成功"

    embed = discord.Embed(
        title="🎯 互動發放 金幣！",
        description="已根據訊息的表情互動紀錄，自動結算並發放金幣！",
        color=0x00BFFF
    )
    embed.set_thumbnail(url=Z_COIN_ICON_URL)
    embed.add_field(name="每人獲得金額", value=f"💰 {amount} 金幣", inline=True)
    embed.add_field(name="管理員", value=ctx.author.display_name, inline=True)
    embed.add_field(name="發放原因", value=reason, inline=False)
    embed.add_field(name=f"✅ 成功發放名單 ({len(success_members)}人)", value=success_str, inline=False)

    if fail_members:
        fail_str = ", ".join(fail_members)
        if len(fail_str) > 1024:
            fail_str = fail_str[:1020] + "..."
        embed.add_field(name="❌ 發放失敗名單", value=fail_str, inline=False)

    embed.add_field(name="🔗 活動訊息連結", value=f"[點擊前往]({target_message.jump_url})", inline=False)
    embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

    # 標註邏輯：改為只標註成功發放對象
    mention_str = " ".join(success_members)
    if len(mention_str) > 1900:
        mention_str = mention_str[:1900] + "..."

    target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))

    if target_channel:
        await target_channel.send(
            content=f"{mention_str} 🎯 **互動獎勵已結算發放！**" if mention_str else None,
            embed=embed
        )
        await status_msg.edit(content=f"✅ 互動發放完畢！成功 {len(success_members)} 人，公告已同步至 {target_channel.mention}。")
    else:
        await ctx.send("❌ 找不到目標頻道，請確認 TARGET_CHANNEL_ID。")


@bot.command(name='測試互動發放')
@commands.has_permissions(administrator=True)
async def test_reaction_airdrop(ctx, message_id: int, amount: int, *, reason: str = "開發者環境測試"):
    """[測試用] 根據目標訊息的表情互動，自動發放金幣給所有互動過的玩家（去重複），公告送至測試頻道"""

    try:
        target_message = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
        await ctx.send("❌ 找不到指定的訊息，請確認 message_id 是否正確，且訊息位於本頻道內。")
        return
    except Exception as e:
        await ctx.send(f"❌ 讀取訊息時發生錯誤：{e}")
        return

    status_msg = await ctx.send("⏳ [測試模式] 正在掃描訊息的表情互動紀錄...")
    unique_users = set()

    for reaction in target_message.reactions:
        async for user in reaction.users():
            if not user.bot:
                unique_users.add(user)

    if not unique_users:
        await status_msg.edit(content="⚠️ 該訊息沒有任何非機器人使用者的表情互動，已取消發放。")
        return

    await status_msg.edit(content=f"⏳ [測試模式] 偵測到 {len(unique_users)} 位互動玩家，正在發放每人 {amount} 金幣...")

    success_members = []
    fail_members = []
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    data = {"cash": amount}

    async with aiohttp.ClientSession() as session:
        for user in unique_users:
            success, status = await _patch_user_balance(session, user.id, data, headers)
            if success:
                success_members.append(user.mention)
            else:
                fail_members.append(user.display_name)
            await asyncio.sleep(0.2)

    if success_members:
        success_str = " ".join(success_members)
        if len(success_str) > 1024:
            success_str = success_str[:1020] + "..."
    else:
        success_str = "無人發放成功"

    # 測試版視覺面板設定 (標題與顏色改為灰色系)
    embed = discord.Embed(
        title="🧪 [測試模式] 互動發放 金幣！",
        description="已根據訊息的表情互動紀錄，自動結算並發放金幣！",
        color=0x95a5a6
    )
    embed.set_thumbnail(url=Z_COIN_ICON_URL)
    embed.add_field(name="每人獲得金額", value=f"💰 {amount} 金幣", inline=True)
    embed.add_field(name="管理員", value=ctx.author.display_name, inline=True)
    embed.add_field(name="發放原因", value=reason, inline=False)
    embed.add_field(name=f"✅ 成功發放名單 ({len(success_members)}人)", value=success_str, inline=False)

    if fail_members:
        fail_str = ", ".join(fail_members)
        if len(fail_str) > 1024:
            fail_str = fail_str[:1020] + "..."
        embed.add_field(name="❌ 發放失敗名單", value=fail_str, inline=False)

    embed.add_field(name="🔗 活動訊息連結", value=f"[點擊前往]({target_message.jump_url})", inline=False)
    embed.set_footer(text="AI 自動日報系統 | 感謝您的支持")

    # 標註邏輯：改為只標註測試成功發放對象
    mention_str = " ".join(success_members)
    if len(mention_str) > 1900:
        mention_str = mention_str[:1900] + "..."

    # 頻道強制指定為測試頻道 (TEST_CHANNEL_ID)
    target_channel = bot.get_channel(int(TEST_CHANNEL_ID))

    if target_channel:
        await target_channel.send(
            content=f"{mention_str} 🧪 **[測試] 互動獎勵已結算發放！**" if mention_str else None,
            embed=embed
        )
        await status_msg.edit(content=f"✅ 測試完畢！成功發放對象：{len(success_members)} 位成員，公告已送至測試頻道 {target_channel.mention}。")
    else:
        await ctx.send("❌ 找不到測試頻道，請確認 TEST_CHANNEL_ID。")


@bot.command(name='全體空投')
@commands.has_permissions(administrator=True)
async def global_airdrop(ctx, amount: int, *, reason: str = "慶祝「AI 自動日報系統」重大里程碑！"):
    """對全伺服器成員進行群發空投（含安全防護與審計紀錄）"""
    global _global_airdrop_running

    # 防護 1：重複執行鎖
    if _global_airdrop_running:
        await ctx.send("⚠️ 全體空投正在執行中，請等待完成後再試。")
        return

    # 防護 2：確保成員快取完整
    if not ctx.guild.chunked:
        await ctx.send("⏳ 正在載入完整成員清單...")
        await ctx.guild.chunk()

    members = [m for m in ctx.guild.members if not m.bot]
    total = len(members)

    # 防護 3：二次確認
    confirm_msg = await ctx.send(
        f"⚠️ **全體空投確認**\n"
        f"▸ 對象：**{total}** 位成員\n"
        f"▸ 金額：每人 **{amount}** 金幣（總計 **{total * amount}** 金幣）\n"
        f"▸ 標題：{reason}\n\n"
        f"確認執行請點 ✅，取消請點 ❌（30 秒內有效）"
    )
    await confirm_msg.add_reaction("✅")
    await confirm_msg.add_reaction("❌")

    def check(reaction, user):
        return (user == ctx.author
                and str(reaction.emoji) in ["✅", "❌"]
                and reaction.message.id == confirm_msg.id)

    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=30.0, check=check)
        if str(reaction.emoji) == "❌":
            await ctx.send("❎ 全體空投已取消。")
            return
    except asyncio.TimeoutError:
        await ctx.send("⏰ 確認逾時，全體空投已取消。")
        return

    # 上鎖 — 開始執行
    _global_airdrop_running = True
    try:
        status_msg = await ctx.send(f"⏳ 正在為 {total} 位成員分發補給... (預計耗時 {int(total * 0.25)} 秒)")

        headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
        data = {"cash": amount}

        # Money Log：發放前快照全員餘額
        await status_msg.edit(content=f"⏳ 正在擷取發放前餘額快照...")
        async with aiohttp.ClientSession() as session:
            balance_snapshot = await _fetch_all_balances(session, headers)

            tz = datetime.timezone(datetime.timedelta(hours=8))
            log_data = {
                "timestamp": datetime.datetime.now(tz).isoformat(),
                "command": "全體空投",
                "operator": ctx.author.display_name,
                "operator_id": str(ctx.author.id),
                "amount": amount,
                "reason": reason,
                "target_count": total,
                "balance_snapshot": [
                    {
                        "user_id": str(u.get("user_id", "")),
                        "rank": u.get("rank", 0),
                        "cash": u.get("cash", 0),
                        "bank": u.get("bank", 0),
                        "total": u.get("total", 0),
                    }
                    for u in balance_snapshot
                ],
                "results": {"success_count": 0, "fail_count": 0, "failures": []}
            }

            await status_msg.edit(content=f"⏳ 餘額快照完成（{len(balance_snapshot)} 筆），開始發放...")

            # 發放金幣
            success_count = 0
            fail_members = []

            for i, member in enumerate(members):
                success, status = await _patch_user_balance(session, member.id, data, headers)
                if success:
                    success_count += 1
                else:
                    fail_members.append({"user_id": str(member.id), "name": member.display_name, "http_status": status})
                await asyncio.sleep(0.2)

                # 每 50 人更新進度
                if (i + 1) % 50 == 0:
                    await status_msg.edit(content=f"⏳ 進度：{i + 1}/{total}（成功 {success_count}）...")

        # 寫入 Money Log
        log_data["results"]["success_count"] = success_count
        log_data["results"]["fail_count"] = len(fail_members)
        log_data["results"]["failures"] = fail_members
        log_path = _save_money_log(log_data)

        # 發送全服公告
        target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))
        if target_channel:
            embed = discord.Embed(
                title=f"🎊 {reason}",
                description=f"感謝大家支持 **AI 自動日報系統**！全體成員皆已獲得金幣專屬補給。",
                color=0xFF4500
            )
            embed.set_thumbnail(url=Z_COIN_ICON_URL)
            embed.add_field(name="每人獲得", value=f"💰 {amount} 金幣", inline=True)
            embed.add_field(name="公告原因", value=reason, inline=False)
            embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

            mention_str = "@everyone" if OFFICIAL_MENTION.lower() == "everyone" else f"<@&{OFFICIAL_MENTION}>"
            await target_channel.send(content=f"{mention_str} 🚁 **全域補給已送達！**", embed=embed)

        # 管理端回報（含失敗名單）
        result_text = f"✅ 全體空投完畢！成功：{success_count}/{total}"
        if fail_members:
            fail_names = [f["name"] for f in fail_members[:20]]
            fail_str = ", ".join(fail_names)
            if len(fail_members) > 20:
                fail_str += f"... 等共 {len(fail_members)} 人"
            result_text += f"\n❌ 失敗名單：{fail_str}"
        result_text += f"\n📋 審計紀錄已儲存"
        await status_msg.edit(content=result_text)

    finally:
        _global_airdrop_running = False


@bot.command(name='測試全體空投')
@commands.has_permissions(administrator=True)
async def test_global_airdrop(ctx, amount: int, *, reason: str = "開發者環境測試"):
    """[測試用] 對全伺服器成員進行群發空投，公告送至測試頻道"""
    global _global_airdrop_running

    # 防護 1：重複執行鎖
    if _global_airdrop_running:
        await ctx.send("⚠️ 全體空投正在執行中，請等待完成後再試。")
        return

    # 防護 2：確保成員快取完整
    if not ctx.guild.chunked:
        await ctx.send("⏳ [測試模式] 正在載入完整成員清單...")
        await ctx.guild.chunk()

    members = [m for m in ctx.guild.members if not m.bot]
    total = len(members)

    # 防護 3：二次確認
    confirm_msg = await ctx.send(
        f"🧪 **[測試] 全體空投確認**\n"
        f"▸ 對象：**{total}** 位成員\n"
        f"▸ 金額：每人 **{amount}** 金幣（總計 **{total * amount}** 金幣）\n"
        f"▸ 標題：{reason}\n\n"
        f"確認執行請點 ✅，取消請點 ❌（30 秒內有效）"
    )
    await confirm_msg.add_reaction("✅")
    await confirm_msg.add_reaction("❌")

    def check(reaction, user):
        return (user == ctx.author
                and str(reaction.emoji) in ["✅", "❌"]
                and reaction.message.id == confirm_msg.id)

    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=30.0, check=check)
        if str(reaction.emoji) == "❌":
            await ctx.send("❎ 測試全體空投已取消。")
            return
    except asyncio.TimeoutError:
        await ctx.send("⏰ 確認逾時，測試全體空投已取消。")
        return

    # 上鎖 — 開始執行
    _global_airdrop_running = True
    try:
        status_msg = await ctx.send(f"⏳ [測試模式] 正在為 {total} 位成員分發補給...")

        headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
        data = {"cash": amount}

        # Money Log：發放前快照
        await status_msg.edit(content=f"⏳ [測試模式] 正在擷取發放前餘額快照...")
        async with aiohttp.ClientSession() as session:
            balance_snapshot = await _fetch_all_balances(session, headers)

            tz = datetime.timezone(datetime.timedelta(hours=8))
            log_data = {
                "timestamp": datetime.datetime.now(tz).isoformat(),
                "command": "測試全體空投",
                "operator": ctx.author.display_name,
                "operator_id": str(ctx.author.id),
                "amount": amount,
                "reason": reason,
                "target_count": total,
                "balance_snapshot": [
                    {
                        "user_id": str(u.get("user_id", "")),
                        "rank": u.get("rank", 0),
                        "cash": u.get("cash", 0),
                        "bank": u.get("bank", 0),
                        "total": u.get("total", 0),
                    }
                    for u in balance_snapshot
                ],
                "results": {"success_count": 0, "fail_count": 0, "failures": []}
            }

            await status_msg.edit(content=f"⏳ [測試模式] 餘額快照完成（{len(balance_snapshot)} 筆），開始發放...")

            # 發放金幣
            success_count = 0
            fail_members = []

            for i, member in enumerate(members):
                success, status = await _patch_user_balance(session, member.id, data, headers)
                if success:
                    success_count += 1
                else:
                    fail_members.append({"user_id": str(member.id), "name": member.display_name, "http_status": status})
                await asyncio.sleep(0.2)

                if (i + 1) % 50 == 0:
                    await status_msg.edit(content=f"⏳ [測試模式] 進度：{i + 1}/{total}（成功 {success_count}）...")

        # 寫入 Money Log
        log_data["results"]["success_count"] = success_count
        log_data["results"]["fail_count"] = len(fail_members)
        log_data["results"]["failures"] = fail_members
        log_path = _save_money_log(log_data)

        # 發送測試頻道公告
        target_channel = bot.get_channel(int(TEST_CHANNEL_ID))
        if target_channel:
            embed = discord.Embed(
                title=f"🧪 [測試] {reason}",
                description=f"感謝大家支持 **AI 自動日報系統**！全體成員皆已獲得金幣專屬補給。",
                color=0x95a5a6
            )
            embed.set_thumbnail(url=Z_COIN_ICON_URL)
            embed.add_field(name="每人獲得", value=f"💰 {amount} 金幣", inline=True)
            embed.add_field(name="公告原因", value=reason, inline=False)
            embed.set_footer(text="AI 自動日報系統 | 感謝你的支持")

            await target_channel.send(content=f"🧪 **[測試] 全域補給已送達！**", embed=embed)

        # 管理端回報
        result_text = f"✅ [測試] 全體空投完畢！成功：{success_count}/{total}"
        if fail_members:
            fail_names = [f["name"] for f in fail_members[:20]]
            fail_str = ", ".join(fail_names)
            if len(fail_members) > 20:
                fail_str += f"... 等共 {len(fail_members)} 人"
            result_text += f"\n❌ 失敗名單：{fail_str}"
        result_text += f"\n📋 審計紀錄已儲存"
        await status_msg.edit(content=result_text)

    finally:
        _global_airdrop_running = False


async def build_total_leaderboard_embed():
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/?sort=total&limit=10"
    headers = {"Authorization": UB_TOKEN, "Accept": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return None
            
            response_data = await response.json()
            data = response_data.get('users', response_data) if isinstance(response_data, dict) else response_data
            
    if not data:
        return None
        
    embed = discord.Embed(
        title="🏆 金幣排行榜",
        description="累積活躍度前五名",
        color=0xFFD700
    )
    embed.set_thumbnail(url=Z_COIN_ICON_URL)
    
    # 排除特定使用者，並取回真正的 Top 5
    exclude_ids = ["1394136025487638608"]
    filtered_data = [u for u in data if str(u.get('user_id')) not in exclude_ids]
    display_data = filtered_data[:5]
    
    medals = ["🥇", "🥈", "🥉", "🏅", "🏅"]
    for idx, user_data in enumerate(display_data):
        user_id = user_data.get('user_id')
        total_cash = user_data.get('total', 0)
        
        # 顯示名稱預設使用提到 (<@ID>)，若是 Bot 緩存有抓到就用 display_name
        user = bot.get_user(int(user_id))
        display_name = user.display_name if user else f"<@{user_id}>"
            
        medal = medals[idx] if idx < len(medals) else "🏅"
        embed.add_field(
            name=f"{medal} 第 {idx+1} 名", 
            value=f"{display_name} - <:Gold_coin:1483028967152681010> **{total_cash}** 金幣", 
            inline=False
        )
        
    embed.set_footer(text="AI 自動日報系統 | 感謝您的支持")
    return embed

@bot.command(name='測試總數排行')
@commands.has_permissions(administrator=True)
async def test_total_leaderboard(ctx):
    """取得伺服器內金幣排名前五名，並發布在測試頻道"""
    status_msg = await ctx.send("⏳ 正在撈取伺服器總金幣排行榜...")
    
    embed = await build_total_leaderboard_embed()
    if not embed:
        await status_msg.edit(content="❌ 無法取得排行榜資料或資料為空。")
        return
    
    target_channel = bot.get_channel(int(TEST_CHANNEL_ID))
    if target_channel:
        await target_channel.send(content="🧪 **[測試] 最新排行榜結算出爐！**", embed=embed)
        await status_msg.edit(content=f"✅ 排行榜已成功發布至測試頻道 {target_channel.mention}！")
    else:
        await status_msg.edit(content="❌ 找不到測試頻道，請確認 TEST_CHANNEL_ID。")
        
@bot.command(name='總數排行')
@commands.has_permissions(administrator=True)
async def total_leaderboard(ctx):
    """取得伺服器內金幣排名前五名，並手動發布在正式頻道"""
    status_msg = await ctx.send("⏳ 正在撈取伺服器總金幣排行榜...")
    
    embed = await build_total_leaderboard_embed()
    if not embed:
        await status_msg.edit(content="❌ 無法取得排行榜資料或資料為空。")
        return
    
    target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))
    if target_channel:
        # 不標註任何人，單純發送文字跟 embed
        await target_channel.send(content="🏆 **金幣排行榜結算出爐！**", embed=embed)
        await status_msg.edit(content=f"✅ 排行榜已成功發布至正式頻道 {target_channel.mention}！")
    else:
        await status_msg.edit(content="❌ 找不到正式頻道，請確認 TARGET_CHANNEL_ID。")

@bot.command(name='測試發公告')
@commands.has_permissions(administrator=True)
async def test_announce(ctx, *, message: str):
    """[測試用] 讓機器人代發客製化文字或公告至測試頻道"""
    target_channel = bot.get_channel(int(TEST_CHANNEL_ID))
    if target_channel:
        await target_channel.send(message)
        await ctx.message.add_reaction("✅")
        await ctx.send(f"✅ 測試公告已成功發佈至 {target_channel.mention}！", delete_after=5)
    else:
        await ctx.send("❌ 找不到測試頻道，請確認 TEST_CHANNEL_ID。")

@bot.command(name='發公告')
@commands.has_permissions(administrator=True)
async def announce(ctx, *, message: str):
    """讓機器人代發客製化文字或公告至正式頻道"""
    target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))
    if target_channel:
        await target_channel.send(message)
        await ctx.message.add_reaction("✅")
        await ctx.send(f"✅ 公告已成功發佈至 {target_channel.mention}！", delete_after=5)
    else:
        await ctx.send("❌ 找不到正式頻道，請確認 TARGET_CHANNEL_ID。")

@bot.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.listening, name="空投指令")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f'[OK] AI 補給系統 [{bot.user.name}] 已連線，專案：AI 自動日報系統')
    
    # 檢查是否為 GitHub Actions 的一次性自動執行模式
    if "--auto-weekly" in sys.argv:
        print("偵測到 --auto-weekly 參數，執行一次性發布流程...")
        tz = datetime.timezone(datetime.timedelta(hours=8))
        now = datetime.datetime.now(tz)
        
        if now.weekday() == 0:  # 0代表星期一 (也就是星期天半夜)
            target_channel = bot.get_channel(int(TARGET_CHANNEL_ID))
            if target_channel:
                embed = await build_total_leaderboard_embed()
                if embed:
                    await target_channel.send(content="🏆 **每週金幣排行榜結算出爐！**", embed=embed)
                    print("✅ 自動每週排行榜已成功發布。")
                else:
                    print("❌ 取得排行榜失敗或資料為空。")
            else:
                print("❌ 找不到正式頻道，跳過發布。")
        else:
            print(f"今天不是星期一 (weekday={now.weekday()})，跳過排行榜發布。")
            
        print("一次性任務結束，關閉機器人...")
        await bot.close()
        return

@official_airdrop.error
@test_airdrop.error
@global_airdrop.error
@test_global_airdrop.error
@multi_airdrop.error 
@test_multi_airdrop.error 
@reaction_airdrop.error
@test_reaction_airdrop.error
@test_total_leaderboard.error
@total_leaderboard.error
@announce.error
@test_announce.error
async def airdrop_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 權限不足：只有系統管理員可以使用此空投權限。")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❓ 格式錯誤：請確認參數是否正確（金額、@對象等）。")

# --- 程序入口 ---
if __name__ == "__main__":
    _acquire_bot_lock()
    atexit.register(_release_bot_lock)
    bot.run(DISCORD_TOKEN)