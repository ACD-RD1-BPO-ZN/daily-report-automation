import discord
import os
import asyncio
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
env_path = os.path.join(parent_dir, '.env') if os.path.exists(os.path.join(parent_dir, '.env')) else os.path.join(current_dir, '.env')
load_dotenv(env_path)

DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
TARGET_CHANNEL_ID = 1481334003372920993

intents = discord.Intents.default()
client = discord.Client(intents=intents)

MESSAGE_CONTENT = """📍【新增頻道】<#1494892454552273016> 邀請大家，來打聲招呼吧 ☕

為了方便社群成員相互認識並提升未來「組團」的效率，請大家撥冗前往新頻道簡單介紹自己。"""

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    
    # 預設發送到公告區
    channel = client.get_channel(TARGET_CHANNEL_ID)
    if channel:
        await channel.send(MESSAGE_CONTENT)
        print(f"Message sent to channel {channel.name} ({TARGET_CHANNEL_ID})")
    else:
        print(f"Failed to find channel with ID {TARGET_CHANNEL_ID}")
        
    await client.close()

if __name__ == "__main__":
    if DISCORD_TOKEN is None:
        print("Error: DISCORD_BOT_TOKEN not found in .env")
    else:
        client.run(DISCORD_TOKEN)
