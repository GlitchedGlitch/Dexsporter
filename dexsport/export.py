import bz2
import os
import time
import traceback
from typing import Any

import discord
from ballsdex.core.models import (
    Ball,
    BallInstance,
    BlacklistedGuild,
    BlacklistedID,
    Economy,
    Friendship,
    GuildConfig,
    Player,
    Regime,
    Special,
    Trade,
    TradeObject,
)

__version__ = "1.0.0"

MIGRATIONS: dict[str, dict[str, Any]] = {
    "R": {"model": Regime, "process": "Regime", "values": ["name", "background"]},
    "E": {"model": Economy, "process": "Economy", "values": ["name", "icon"]},
    "S": {
        "model": Special,
        "process": "Special",
        "values": [
            "name", "catch_phrase", "start_date", "end_date", "rarity",
            "background", "emoji", "tradeable", "hidden", "credits",
        ],
        "defaults": {
            "catch_phrase": None, "start_date": None, "end_date": None,
            "background": None, "emoji": None, "credits": None,
        },
    },
    "B": {
        "model": Ball,
        "process": "Ball",
        "values": [
            "regime_id", "economy_id", "country", "short_name", "catch_names",
            "translations", "health", "attack", "rarity", "enabled", "tradeable",
            "emoji_id", "wild_card", "collection_card", "credits",
            "capacity_name", "capacity_description",
        ],
        "defaults": {
            "economy_id": None, "short_name": None, "catch_names": None,
            "translations": None, "enabled": True, "tradeable": True,
        },
    },
    "P": {
        "model": Player,
        "process": "Player",
        "values": ["discord_id", "donation_policy", "privacy_policy"],
        "defaults": {"donation_policy": 1, "privacy_policy": 2},
    },
    "BI": {
        "model": BallInstance,
        "process": "BallInstance",
        "values": [
            "ball_id", "player_id", "catch_date", "spawned_time", "server_id",
            "special_id", "health_bonus", "attack_bonus", "trade_player_id",
            "favorite", "tradeable",
        ],
        "defaults": {
            "spawned_time": None, "server_id": None, "special_id": None,
            "trade_player_id": None, "favorite": False, "tradeable": True,
        },
    },
    "GC": {
        "model": GuildConfig,
        "process": "GuildConfig",
        "values": ["guild_id", "spawn_channel", "enabled"],
        "defaults": {"spawn_channel": None, "enabled": True},
    },
    "F": {"model": Friendship, "process": "Friendship", "values": ["player1_id", "player2_id", "since"]},
    "BU": {
        "model": BlacklistedID,
        "process": "BlacklistedID",
        "values": ["discord_id", "reason", "date"],
        "defaults": {"reason": None, "date": None},
    },
    "BG": {
        "model": BlacklistedGuild,
        "process": "BlacklistedGuild",
        "values": ["discord_id", "reason", "date"],
        "defaults": {"reason": None, "date": None},
    },
    "T": {"model": Trade, "process": "Trade", "values": ["player1_id", "player2_id", "date"]},
    "TO": {"model": TradeObject, "process": "TradeObject", "values": ["trade_id", "ballinstance_id", "player_id"]},
}

output = []


def reload_embed(start_time: float | None = None, file: str | None = None, status="RUNNING"):
    embed = discord.Embed(title="Dexsporter — Export", description=f"Status: **{status}**")
    match status:
        case "RUNNING":
            embed.color = discord.Color.yellow()
        case "FINISHED":
            embed.color = discord.Color.green()
        case "CANCELED":
            embed.color = discord.Color.red()

    if output:
        embed.add_field(name="Output", value="\n".join(output[-20:]))

    if file:
        embed.add_field(
            name="File",
            value=f"Saved to `{file}` ({convert_size(os.path.getsize(file))})",
            inline=False,
        )

    if start_time is not None:
        embed.set_footer(text=f"Finished in {round(time.time() - start_time, 3)}s")

    return embed


def convert_size(b: int) -> str:
    if b < 1024:
        return f"{b} bytes"
    if b < 1024**2:
        return f"{b / 1024:.2f} KB"
    if b < 1024**3:
        return f"{b / 1024**2:.2f} MB"
    return f"{b / 1024**3:.2f} GB"


async def process(entry: str, migration: dict) -> str:
    content = []
    first_instance = True
    has_defaults = "defaults" in migration
    rename = migration.get("rename", {})
    model = migration["model"]

    model_fields = set(model._meta.fields_map.keys())

    seen = {"id"}
    values = ["id"]
    for v in migration["values"]:
        if v not in seen and (v in model_fields or v.endswith("_id")):
            seen.add(v)
            values.append(v)
    if has_defaults:
        for v in migration["defaults"].keys():
            if v not in seen and (v in model_fields or v.endswith("_id")):
                seen.add(v)
                values.append(v)

    rows = [x async for x in model.all().order_by("id").values_list(*values)]

    for row in rows:
        row = tuple(
            (str(v) if str(v) else None) if hasattr(v, "name") and hasattr(v, "url") else v
            for v in row
        )
        model_dict = dict(zip(values, row))
        fields = []

        for key, value in model_dict.items():
            if has_defaults and key in migration["defaults"]:
                default = migration["defaults"][key]
                effective_value = None if value == "" else value
                if effective_value == default:
                    fields.append("")
                    continue

            value_string = str(value) if value is not None else "None"

            if value_string == "True":
                value_string = "🬀"
            elif value_string == "False":
                value_string = "🬁"

            fields.append(
                value_string.replace("\r\n", "🮈").replace("\r", "🮈").replace("\n", "🮈").replace("╵", "🮉")
            )

        if first_instance:
            content.append(f":{entry}")
            renamed_values = [rename.get(v, v) for v in values]
            content.append(f"#fields:{'╵'.join(renamed_values)}")
            first_instance = False

        content.append("╵".join(fields))

    count = await model.all().count()
    output.append(f"- Exported **{count:,}** {migration['process']} objects.")

    return "\n".join(content)


async def migrate(message, filename: str) -> str | None:
    with bz2.open(f"{filename}.bz2", "wt", encoding="utf-8") as f:
        content = [
            f"// Generated with 'Dexsporter' v{__version__}\n"
            "// Run import.py on your target BallsDex bot to import this data.\n\n"
        ]
        error_occurred = False

        for key, migration in MIGRATIONS.items():
            try:
                field = await process(key, migration)
            except Exception:
                print(f"Error processing {key}:\n{traceback.format_exc()}")
                error_occurred = True
                break

            content.append(field)
            await message.edit(embed=reload_embed())

        if error_occurred:
            return

        f.write("\n".join(content))

    return f"{filename}.bz2"


async def main():
    message = await ctx.send(embed=reload_embed())  # type: ignore # noqa: F821
    start_time = time.time()

    path = await migrate(message, "/tmp/dexport.txt")

    if path is None:
        await message.edit(embed=reload_embed(start_time, status="CANCELED"))
        return

    await message.edit(embed=reload_embed(start_time, path, "FINISHED"))

    try:
        await ctx.send(  # type: ignore # noqa: F821
            "**Export file — drag this into your target BallsDex bot's `/code/` folder:**",
            file=discord.File(path),
        )
    except discord.HTTPException:
        size = convert_size(os.path.getsize(path))
        await ctx.send(  # type: ignore # noqa: F821
            f"File too large to upload ({size}). Copy `{path}` manually to the target bot's `/code/` folder."
        )


await main()  # type: ignore # noqa: F704
