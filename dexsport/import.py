import asyncio
import bz2
import os
import shutil
import time
from datetime import datetime, date

import discord
from tortoise import Tortoise
from tortoise.fields.data import DatetimeField, DateField, FloatField, IntField
from tortoise.exceptions import ValidationError

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

try:
    from ballsdex.core.models import DonationPolicy, PrivacyPolicy
except ImportError:
    DonationPolicy = None
    PrivacyPolicy = None

__version__ = "1.0.0"


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_datetime(value):
    if value in (None, "", "None"):
        return None
    if isinstance(value, datetime):
        return value
    try:
        f = float(value)
        if 0 <= f <= 4_102_444_800:
            return datetime.fromtimestamp(f)
    except (TypeError, ValueError, OSError):
        pass
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def safe_date(value):
    if value in (None, "", "None"):
        return None
    if isinstance(value, date):
        return value
    try:
        f = float(value)
        if f > 10_000_000_000:
            return date.fromtimestamp(f)
        return None
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


SECTIONS = {
    "R": [Regime, None],
    "E": [Economy, None],
    "S": [Special, None],
    "B": [Ball, None],
    "P": [Player, None],
    "BI": [BallInstance, None],
    "GC": [GuildConfig, None],
    "F": [Friendship, None],
    "BU": [BlacklistedID, None],
    "BG": [BlacklistedGuild, None],
    "T": [Trade, None],
    "TO": [TradeObject, None],
}

output = []


def reload_embed(start_time: float | None = None, status="RUNNING"):
    embed = discord.Embed(title="Dexsporter — Import", description=f"Status: **{status}**")
    if status == "RUNNING":
        embed.color = discord.Color.yellow()
    elif status == "FINISHED":
        embed.color = discord.Color.green()
    elif status == "CANCELED":
        embed.color = discord.Color.red()

    if output:
        recent = output[-20:]
        text = "\n".join(recent)
        if len(text) > 1000:
            text = "...\n" + text[-1000:]
        embed.add_field(name="Output", value=text)

    if start_time is not None:
        embed.set_footer(text=f"Finished in {round(time.time() - start_time, 3)}s")

    return embed


def read_bz2(path: str):
    with bz2.open(path, "rb") as f:
        return f.read().splitlines()


async def load(message):
    lines = read_bz2("/code/dexport.txt.bz2")
    section = ""
    data = {}

    skipped_log = open("/code/skipped_records.log", "w", encoding="utf-8")
    skipped_log.write("=== DEXSPORTER SKIPPED RECORDS ===\n")
    skipped_log.write(f"Generated: {datetime.now()}\n\n")

    output.append(f"- Reading export file with {len(lines):,} lines...")
    await message.edit(embed=reload_embed())

    for index, line in enumerate(lines, start=1):
        line = line.decode().rstrip()

        if index % 10000 == 0:
            output[-1] = f"- Reading export file... ({index:,}/{len(lines):,})"
            await message.edit(embed=reload_embed())

        if line.startswith("//") or line == "":
            continue

        if line.startswith(":"):
            section = line[1:]
            if section not in SECTIONS:
                raise Exception(f"Invalid section '{section}' on line {index}")
            continue

        if line.startswith("#fields:"):
            col_names = line[len("#fields:"):].split("╵")
            if section in SECTIONS:
                SECTIONS[section][1] = col_names
            continue

        if line.startswith("#"):
            continue

        if section == "" or SECTIONS[section][1] is None:
            continue

        section_full = SECTIONS[section]
        bucket_key = (section_full[0], section)

        if bucket_key not in data:
            data[bucket_key] = []

        model_dict = {}
        fields_map = section_full[0]._meta.fields_map

        for value, line_data in zip(section_full[1], line.split("╵")):
            if value == "id" and line_data == "":
                model_dict = None
                break

            if line_data == "":
                model_dict[value] = None
                continue

            if value not in fields_map:
                model_dict[value] = line_data if line_data not in ("None",) else None
                continue

            if line_data == "None":
                line_data = None
            elif line_data == "🬀":
                line_data = True
            elif line_data == "🬁":
                line_data = False

            if line_data is not None:
                field_type = fields_map[value]
                if isinstance(field_type, IntField):
                    line_data = safe_int(line_data)
                elif isinstance(field_type, FloatField):
                    try:
                        line_data = float(line_data)
                    except (ValueError, TypeError):
                        line_data = 0.0
                elif isinstance(field_type, DatetimeField):
                    line_data = safe_datetime(line_data)
                elif isinstance(field_type, DateField):
                    line_data = safe_date(line_data)

            if isinstance(line_data, str):
                line_data = line_data.replace("🮈", "\n").replace("🮉", "╵")

            model_dict[value] = line_data

        if model_dict is not None:
            model_dict["_section"] = section
            data[bucket_key].append(model_dict)

    output.append("- Finished reading. Processing models...")
    await message.edit(embed=reload_embed())

    start_time = time.time()
    inserted_ids = {}

    processing_order = [
        (Regime, "R"),
        (Economy, "E"),
        (Special, "S"),
        (Ball, "B"),
        (Player, "P"),
        (BallInstance, "BI"),
        (GuildConfig, "GC"),
        (Friendship, "F"),
        (BlacklistedID, "BU"),
        (BlacklistedGuild, "BG"),
        (Trade, "T"),
        (TradeObject, "TO"),
    ]

    for (item, section_key) in processing_order:
        bucket_key = (item, section_key)
        if bucket_key not in data:
            continue

        rows = data[bucket_key]
        output.append(f"- Processing {item.__name__}... ({len(rows):,} records)")
        await message.edit(embed=reload_embed())

        fields_map = item._meta.fields_map

        fk_fields = {}
        for field_name, field_obj in fields_map.items():
            if hasattr(field_obj, "related_model") and field_obj.related_model is not None:
                fk_fields[field_name] = field_obj.related_model
                fk_fields[field_name + "_id"] = field_obj.related_model

        seen_ids = set()
        unique_values = []
        skipped = 0
        fk_violations = 0
        null_violations = 0
        duplicates = 0

        for model in rows:
            model_id = model.get("id")
            model.pop("_section", None)

            for f in list(model.keys()):
                if f not in fields_map and f != "id":
                    model.pop(f, None)

            if model_id is None:
                skipped += 1
                continue

            if item == Player:
                did = model.get("discord_id")
                try:
                    valid = 17 <= len(str(int(did))) <= 19
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    skipped_log.write(f"Player {model_id}: SKIPPED invalid discord_id={did}\n")
                    skipped += 1
                    continue

            if model_id in seen_ids:
                skipped += 1
                duplicates += 1
                continue

            has_invalid_fk = False
            for fk_field, related_model in fk_fields.items():
                fk_val = model.get(fk_field)
                if fk_val is None:
                    continue
                if fk_val == 0:
                    base = fk_field[:-3] if fk_field.endswith("_id") else fk_field
                    fo = fields_map.get(base)
                    if fo and getattr(fo, "null", False):
                        model[fk_field] = None
                    else:
                        has_invalid_fk = True
                        fk_violations += 1
                    continue

                in_batch = related_model == item and fk_val in seen_ids
                in_tracking = related_model in inserted_ids and fk_val in inserted_ids[related_model]
                if not in_batch and not in_tracking:
                    exists = await related_model.filter(pk=fk_val).exists()
                    if not exists:
                        if related_model == Player:
                            skipped_log.write(f"{item.__name__} {model_id}: SKIPPED player_id={fk_val} not found\n")
                            has_invalid_fk = True
                            fk_violations += 1
                            break
                        elif related_model == Special:
                            model[fk_field] = None
                        else:
                            skipped_log.write(f"{item.__name__} {model_id}: SKIPPED FK {fk_field}={fk_val} not found\n")
                            has_invalid_fk = True
                            fk_violations += 1
                            break

            if has_invalid_fk:
                skipped += 1
                continue

            skip_record = False
            null_fields = []
            for field_name, field_value in list(model.items()):
                if field_value is None and field_name in fields_map:
                    fo = fields_map[field_name]
                    if hasattr(fo, "null") and not fo.null:
                        if field_name in ("country", "short_name", "capacity_name",
                                          "capacity_description", "credits", "catch_phrase",
                                          "name", "background", "icon"):
                            model[field_name] = "Unknown"
                        elif field_name in ("enabled", "tradeable"):
                            model[field_name] = True
                        elif field_name in ("hidden", "favorite"):
                            model[field_name] = False
                        elif field_name in ("health", "attack", "health_bonus", "attack_bonus"):
                            model[field_name] = 0
                        elif field_name == "rarity":
                            model[field_name] = 0.0
                        elif field_name == "emoji_id":
                            model[field_name] = 1234567890123456789
                        elif field_name == "regime_id":
                            first = await Regime.all().first()
                            model[field_name] = first.pk if first else 1
                        elif field_name == "donation_policy":
                            model[field_name] = list(DonationPolicy)[0] if DonationPolicy else 1
                        elif field_name == "privacy_policy":
                            model[field_name] = list(PrivacyPolicy)[0] if PrivacyPolicy else 1
                        elif field_name == "guild_id":
                            null_fields.append(field_name)
                            skip_record = True
                        else:
                            null_fields.append(field_name)
                            skip_record = True

            if skip_record:
                skipped_log.write(f"{item.__name__} {model_id}: SKIPPED null fields: {', '.join(null_fields)}\n")
                skipped += 1
                null_violations += 1
                continue

            if item == Player:
                dp = model.get("donation_policy")
                pp = model.get("privacy_policy")
                if DonationPolicy and dp is not None:
                    try:
                        model["donation_policy"] = DonationPolicy(int(dp))
                    except (ValueError, KeyError):
                        model["donation_policy"] = list(DonationPolicy)[0]
                if PrivacyPolicy and pp is not None:
                    try:
                        model["privacy_policy"] = PrivacyPolicy(int(pp))
                    except (ValueError, KeyError):
                        model["privacy_policy"] = list(PrivacyPolicy)[0]

            seen_ids.add(model_id)
            unique_values.append(model)

        output[-1] = f"- Creating {item.__name__} instances... ({len(unique_values):,} valid)"
        await message.edit(embed=reload_embed())

        items_to_create = []
        val_failures = 0

        for model in unique_values:
            if model.get("short_name") is None:
                model["short_name"] = None
            if item == Special:
                if model.get("rarity") is None:
                    model["rarity"] = 0.0
                if model.get("tradeable") is None:
                    model["tradeable"] = True
                if model.get("hidden") is None:
                    model["hidden"] = False
            if item == Ball:
                if model.get("country") is None:
                    model["country"] = "Unknown"
                if model.get("enabled") is None:
                    model["enabled"] = True
                if model.get("tradeable") is None:
                    model["tradeable"] = True
                emoji_id = model.get("emoji_id")
                if emoji_id is not None:
                    try:
                        if not (17 <= len(str(int(emoji_id))) <= 19):
                            model["emoji_id"] = 1234567890123456789
                    except (ValueError, TypeError):
                        model["emoji_id"] = 1234567890123456789

            try:
                instance = item(**model)
                try:
                    await instance.full_clean()
                except AttributeError:
                    pass
                except ValidationError as ve:
                    skipped_log.write(f"{item.__name__} {model.get('id')}: SKIPPED validation: {str(ve)[:200]}\n")
                    skipped += 1
                    val_failures += 1
                    continue
                items_to_create.append(instance)
            except (ValueError, ValidationError, TypeError) as e:
                skipped_log.write(f"{item.__name__} {model.get('id')}: SKIPPED error: {str(e)[:200]}\n")
                skipped += 1
                val_failures += 1

        output[-1] = f"- Saving {item.__name__} to DB... ({len(items_to_create):,} objects)"
        await message.edit(embed=reload_embed())

        if items_to_create:
            try:
                await item.bulk_create(items_to_create)
                inserted_ids[item] = seen_ids
                await sequence_model(item)
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)[:300]}"
                output.append(f"- CRITICAL ERROR saving {item.__name__}: {error_msg}")
                skipped_log.write(f"\n{item.__name__} BULK CREATE FAILED: {error_msg}\n")
                await message.edit(embed=reload_embed())
                skipped_log.close()
                raise

        msg = f"- Added **{len(items_to_create):,}** {item.__name__} objects."
        details = []
        if fk_violations:
            details.append(f"{fk_violations} FK violations")
        if null_violations:
            details.append(f"{null_violations} null fields")
        if duplicates:
            details.append(f"{duplicates} duplicates")
        if val_failures:
            details.append(f"{val_failures} validation errors")
        if details:
            msg += f" (skipped: {', '.join(details)})"
        output[-1] = msg
        await message.edit(embed=reload_embed())

    output.append("- Updating DB sequences...")
    await message.edit(embed=reload_embed())
    await sequence_all_models()

    skipped_log.write("\n=== END ===\n")
    skipped_log.close()

    try:
        shutil.copy("/code/skipped_records.log", "/mnt/user-data/outputs/skipped_records.log")
        output.append("- Done! Logs saved.")
    except Exception:
        output.append("- Done! Check skipped_records.log for details.")

    await message.edit(embed=reload_embed(start_time, "FINISHED"))

    try:
        log_path = "/code/skipped_records.log"
        if os.path.exists(log_path) and os.path.getsize(log_path) > 100:
            await ctx.send(file=discord.File(log_path))  # type: ignore # noqa: F821
    except Exception:
        pass

    skipped_b = skipped_p = skipped_bi = 0
    try:
        with open("/code/skipped_records.log", encoding="utf-8") as f:
            for line in f:
                if "Ball " in line and "SKIPPED" in line:
                    skipped_b += 1
                elif "Player " in line and "SKIPPED" in line:
                    skipped_p += 1
                elif "BallInstance " in line and "SKIPPED" in line:
                    skipped_bi += 1
    except Exception:
        pass

    if skipped_b or skipped_p or skipped_bi:
        msg = "**Skipped Records:**\n"
        if skipped_b:
            msg += f"- **{skipped_b} Balls**: Null/invalid required fields\n"
        if skipped_p:
            msg += f"- **{skipped_p} Players**: Invalid Discord ID\n"
        if skipped_bi:
            msg += f"- **{skipped_bi} BallInstances**: Missing player/ball or null fields\n"
        await ctx.send(msg)  # type: ignore # noqa: F821


async def sequence_model(model):
    if await model.all().count() == 0:
        return
    try:
        client = Tortoise.get_connection("default")
        last_id = await model.all().order_by("-id").first().values_list("id", flat=True)
        await client.execute_query(f"SELECT setval('{model._meta.db_table}_id_seq', {last_id});")
    except Exception:
        pass


async def sequence_all_models():
    models = Tortoise.apps.get("models")
    if models is None:
        return
    for model in models.values():
        await sequence_model(model)


async def clear_all_data():
    client = Tortoise.get_connection("default")
    all_models = [
        Regime, Economy, Special, Ball, Player, GuildConfig,
        Friendship, BlacklistedID, BlacklistedGuild, BallInstance, Trade, TradeObject,
    ]
    tables = ", ".join(m._meta.db_table for m in all_models)
    try:
        await client.execute_query(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE;")
    except Exception as e:
        output.append(f"- TRUNCATE failed, using fallback: {str(e)[:100]}")
        for m in reversed(all_models):
            await m.all().delete()
        for m in all_models:
            try:
                await client.execute_query(f"ALTER SEQUENCE {m._meta.db_table}_id_seq RESTART WITH 1;")
            except Exception:
                pass


async def main():
    if not os.path.isfile("/code/dexport.txt.bz2"):
        await ctx.send("`/code/dexport.txt.bz2` not found. Run export.py on your source bot first.")  # type: ignore # noqa: F821
        return

    try:
        await ctx.send(  # type: ignore # noqa: F821
            "**⚠️ WARNING**: All existing data on this bot will be **CLEARED**.\n"
            "Type `proceed` to continue or `cancel` to abort."
        )
        confirm = await bot.wait_for(  # type: ignore # noqa: F821
            "message",
            check=lambda m: m.author == ctx.author  # type: ignore # noqa: F821
            and m.channel == ctx.channel  # type: ignore # noqa: F821
            and m.content.lower() in ["proceed", "cancel"],
            timeout=20,
        )
    except asyncio.TimeoutError:
        await ctx.send("Canceled: response timeout.")  # type: ignore # noqa: F821
        return

    if confirm.content.lower() != "proceed":
        await ctx.send("Canceled.")  # type: ignore # noqa: F821
        return

    message = await ctx.send(embed=reload_embed())  # type: ignore # noqa: F821

    output.append("- Clearing existing data...")
    await message.edit(embed=reload_embed())
    await clear_all_data()

    output.append("- Data cleared. Starting import...")
    await message.edit(embed=reload_embed())

    await load(message)


await main()  # type: ignore # noqa: F704
