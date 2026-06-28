# Dexsporter
too lazy to make a description. This is used to switch ballsdex v2 versions. Trying to get some outdated dexes into v2.30.0. Super special credits for Cayla (or Caylies) for the code inspiration, very smart and creative person 

## export
```py
import base64, requests
r = requests.get("https://raw.githubusercontent.com/GlitchedGlitch/Dexsporter/main/dexsport/export.py")
await ctx.invoke(bot.get_command("eval"), body=r.text)
```

## import
```py
import base64, requests
r = requests.get("https://raw.githubusercontent.com/GlitchedGlitch/Dexsporter/main/dexsport/import.py")
await ctx.invoke(bot.get_command("eval"), body=r.text)
```
