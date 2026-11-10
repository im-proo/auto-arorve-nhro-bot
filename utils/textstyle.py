"""Unicode text styling for the bot's own copy.

Two families, matching the brand's launch style:

  * bold sans-serif for titles      "𝐅𝐢𝐧𝐚𝐥𝐥𝐲 𝐋𝐚𝐮𝐧𝐜𝐡𝐞𝐝"
  * small caps for labels and body  "Iɴꜱᴛᴀɢʀᴀᴍ & Tᴇʟᴇɢʀᴀᴍ"

Only the bot's static copy is transformed. Dynamic values (group names,
usernames, anything a person typed) are never passed through these helpers, so
user content keeps its own script and casing.
"""

import string

_BOLD_UPPER = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙"
_BOLD_LOWER = "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"
_BOLD_DIGITS = "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗"
_SMALL_CAPS = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"

_BOLD = {letter: fancy for letter, fancy in zip(string.ascii_uppercase, _BOLD_UPPER)}
_BOLD.update({letter: fancy for letter, fancy in zip(string.ascii_lowercase, _BOLD_LOWER)})
_BOLD.update({digit: fancy for digit, fancy in zip(string.digits, _BOLD_DIGITS)})
_CAPS = {letter: fancy for letter, fancy in zip(string.ascii_lowercase, _SMALL_CAPS)}


def bold(text):
    """Mathematical bold sans-serif: 'Finally' -> '𝐅𝐢𝐧𝐚𝐥𝐥𝐲'."""
    return "".join(_BOLD.get(char, char) for char in str(text))


def small_caps(text):
    """Small caps for lowercase letters; capitals stay as they are.

    'Instagram' -> 'Iɴꜱᴛᴀɢʀᴀᴍ', 'Telegram' -> 'Tᴇʟᴇɢʀᴀᴍ'.
    """
    return "".join(_CAPS.get(char, char) for char in str(text))


# Readable aliases used by the templates.
title = bold
label = small_caps
