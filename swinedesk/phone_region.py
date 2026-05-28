"""Infer US state or country from a phone number (best-effort).

Mirrors the Java PhoneNumberUtils.inferRegionFromPhone helper so the SMS app
can enrich broker alerts without an extra backend round-trip.
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")

# Country code -> country name. Checked when the number is not NANP (+1).
COUNTRY_CODES: dict[str, str] = {
    "52": "Mexico",
    "44": "United Kingdom",
    "33": "France",
    "49": "Germany",
    "34": "Spain",
    "39": "Italy",
    "31": "Netherlands",
    "32": "Belgium",
    "351": "Portugal",
    "353": "Ireland",
    "45": "Denmark",
    "46": "Sweden",
    "47": "Norway",
    "48": "Poland",
    "55": "Brazil",
    "54": "Argentina",
    "56": "Chile",
    "57": "Colombia",
    "51": "Peru",
    "58": "Venezuela",
    "502": "Guatemala",
    "503": "El Salvador",
    "504": "Honduras",
    "505": "Nicaragua",
    "506": "Costa Rica",
    "507": "Panama",
    "86": "China",
    "81": "Japan",
    "82": "South Korea",
    "84": "Vietnam",
    "63": "Philippines",
    "65": "Singapore",
    "66": "Thailand",
    "91": "India",
    "61": "Australia",
    "64": "New Zealand",
}

# Canadian NANP area codes — these still start with +1 but map to "Canada".
_CANADA_AREA_CODES: set[str] = {
    "204", "226", "236", "249", "250", "289", "306", "343", "365", "403",
    "416", "418", "431", "438", "450", "506", "514", "519", "548", "579",
    "581", "587", "604", "613", "639", "647", "672", "705", "709", "778",
    "780", "782", "807", "819", "825", "867", "873", "902", "905",
}

# US area code -> 2-letter state code.
_US_AREA_CODES: dict[str, str] = {
    # AL
    "205": "AL", "251": "AL", "256": "AL", "334": "AL", "938": "AL", "659": "AL",
    # AK
    "907": "AK",
    # AZ
    "480": "AZ", "520": "AZ", "602": "AZ", "623": "AZ", "928": "AZ",
    # AR
    "479": "AR", "501": "AR", "870": "AR",
    # CA
    "209": "CA", "213": "CA", "279": "CA", "310": "CA", "323": "CA", "341": "CA",
    "408": "CA", "415": "CA", "424": "CA", "442": "CA", "510": "CA", "530": "CA",
    "559": "CA", "562": "CA", "619": "CA", "626": "CA", "628": "CA", "650": "CA",
    "657": "CA", "661": "CA", "669": "CA", "707": "CA", "714": "CA", "747": "CA",
    "760": "CA", "805": "CA", "818": "CA", "820": "CA", "831": "CA", "840": "CA",
    "858": "CA", "909": "CA", "916": "CA", "925": "CA", "949": "CA", "951": "CA",
    # CO
    "303": "CO", "719": "CO", "720": "CO", "970": "CO", "983": "CO",
    # CT
    "203": "CT", "475": "CT", "860": "CT", "959": "CT",
    # DE
    "302": "DE",
    # DC
    "202": "DC",
    # FL
    "239": "FL", "305": "FL", "321": "FL", "352": "FL", "386": "FL", "407": "FL",
    "561": "FL", "656": "FL", "689": "FL", "727": "FL", "754": "FL", "772": "FL",
    "786": "FL", "813": "FL", "850": "FL", "863": "FL", "904": "FL", "941": "FL",
    "954": "FL",
    # GA
    "229": "GA", "404": "GA", "470": "GA", "478": "GA", "678": "GA", "706": "GA",
    "762": "GA", "770": "GA", "912": "GA", "943": "GA",
    # HI
    "808": "HI",
    # ID
    "208": "ID", "986": "ID",
    # IL
    "217": "IL", "224": "IL", "309": "IL", "312": "IL", "331": "IL", "447": "IL",
    "464": "IL", "618": "IL", "630": "IL", "708": "IL", "773": "IL", "779": "IL",
    "815": "IL", "847": "IL", "872": "IL",
    # IN
    "219": "IN", "260": "IN", "317": "IN", "463": "IN", "574": "IN", "765": "IN",
    "812": "IN", "930": "IN",
    # IA
    "319": "IA", "515": "IA", "563": "IA", "641": "IA", "712": "IA",
    # KS
    "316": "KS", "620": "KS", "785": "KS", "913": "KS",
    # KY
    "270": "KY", "364": "KY", "502": "KY", "606": "KY", "859": "KY",
    # LA
    "225": "LA", "318": "LA", "337": "LA", "504": "LA", "985": "LA",
    # ME
    "207": "ME",
    # MD
    "227": "MD", "240": "MD", "301": "MD", "410": "MD", "443": "MD", "667": "MD",
    # MA
    "339": "MA", "351": "MA", "413": "MA", "508": "MA", "617": "MA", "774": "MA",
    "781": "MA", "857": "MA", "978": "MA",
    # MI
    "231": "MI", "248": "MI", "269": "MI", "313": "MI", "517": "MI", "586": "MI",
    "616": "MI", "679": "MI", "734": "MI", "810": "MI", "906": "MI", "947": "MI",
    "989": "MI",
    # MN
    "218": "MN", "320": "MN", "507": "MN", "612": "MN", "651": "MN", "763": "MN",
    "952": "MN",
    # MS
    "228": "MS", "601": "MS", "662": "MS", "769": "MS",
    # MO
    "314": "MO", "417": "MO", "557": "MO", "573": "MO", "636": "MO", "660": "MO",
    "816": "MO", "975": "MO",
    # MT
    "406": "MT",
    # NE
    "308": "NE", "402": "NE", "531": "NE",
    # NV
    "702": "NV", "725": "NV", "775": "NV",
    # NH
    "603": "NH",
    # NJ
    "201": "NJ", "551": "NJ", "609": "NJ", "640": "NJ", "732": "NJ", "848": "NJ",
    "856": "NJ", "862": "NJ", "908": "NJ", "973": "NJ",
    # NM
    "505": "NM", "575": "NM",
    # NY
    "212": "NY", "315": "NY", "332": "NY", "347": "NY", "363": "NY", "516": "NY",
    "518": "NY", "585": "NY", "607": "NY", "631": "NY", "646": "NY", "680": "NY",
    "716": "NY", "718": "NY", "838": "NY", "845": "NY", "914": "NY", "917": "NY",
    "929": "NY", "934": "NY",
    # NC
    "252": "NC", "336": "NC", "472": "NC", "704": "NC", "743": "NC", "828": "NC",
    "910": "NC", "919": "NC", "980": "NC", "984": "NC",
    # ND
    "701": "ND",
    # OH
    "216": "OH", "220": "OH", "234": "OH", "283": "OH", "326": "OH", "330": "OH",
    "380": "OH", "419": "OH", "440": "OH", "513": "OH", "567": "OH", "614": "OH",
    "740": "OH", "937": "OH",
    # OK
    "405": "OK", "539": "OK", "572": "OK", "580": "OK", "918": "OK",
    # OR
    "458": "OR", "503": "OR", "541": "OR", "971": "OR",
    # PA
    "215": "PA", "223": "PA", "267": "PA", "272": "PA", "412": "PA", "445": "PA",
    "484": "PA", "570": "PA", "582": "PA", "610": "PA", "717": "PA", "724": "PA",
    "814": "PA", "835": "PA", "878": "PA",
    # RI
    "401": "RI",
    # SC
    "803": "SC", "821": "SC", "839": "SC", "843": "SC", "854": "SC", "864": "SC",
    # SD
    "605": "SD",
    # TN
    "423": "TN", "615": "TN", "629": "TN", "731": "TN", "865": "TN", "901": "TN",
    "931": "TN",
    # TX
    "210": "TX", "214": "TX", "254": "TX", "281": "TX", "325": "TX", "346": "TX",
    "361": "TX", "409": "TX", "430": "TX", "432": "TX", "469": "TX", "512": "TX",
    "682": "TX", "713": "TX", "726": "TX", "737": "TX", "806": "TX", "817": "TX",
    "830": "TX", "832": "TX", "903": "TX", "915": "TX", "936": "TX", "940": "TX",
    "945": "TX", "956": "TX", "972": "TX", "979": "TX",
    # UT
    "385": "UT", "435": "UT", "801": "UT",
    # VT
    "802": "VT",
    # VA
    "276": "VA", "434": "VA", "540": "VA", "571": "VA", "703": "VA", "757": "VA",
    "804": "VA", "826": "VA", "948": "VA",
    # WA
    "206": "WA", "253": "WA", "360": "WA", "425": "WA", "509": "WA", "564": "WA",
    # WV
    "304": "WV", "681": "WV",
    # WI
    "262": "WI", "274": "WI", "414": "WI", "534": "WI", "608": "WI", "715": "WI",
    "920": "WI",
    # WY
    "307": "WY",
}


def infer_region_from_phone(phone: str | None) -> str | None:
    """Best-effort region (US state or country) from a phone number, or None."""
    if not phone:
        return None
    digits = _DIGITS.sub("", phone)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif len(digits) == 10:
        pass
    else:
        # International — match longest country code prefix first.
        for prefix in sorted(COUNTRY_CODES.keys(), key=len, reverse=True):
            if digits.startswith(prefix):
                return COUNTRY_CODES[prefix]
        return None
    if len(digits) < 3:
        return None
    area = digits[:3]
    if area in _CANADA_AREA_CODES:
        return "Canada"
    return _US_AREA_CODES.get(area)
