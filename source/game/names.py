"""姓名与国籍库。

全部为虚构组合：从各国常见的名与姓池里随机拼装，不使用任何真实车手的
全名。这既是法务要求（真实人物姓名会阻碍上架），也让每次新开档的世界
都不一样。

国籍分布按职业公路车的真实版图加权——比利时、法国、意大利、西班牙、
荷兰是大户，哥伦比亚出爬坡手，丹麦和斯洛文尼亚近年产出总成绩核心。
分布本身就是一种世界观表达，玩家会注意到。
"""

from __future__ import annotations

import random

# 国家代码 -> (中文名, 权重, 名池, 姓池)
NATIONS: dict[str, tuple[str, int, list[str], list[str]]] = {
    "BEL": ("比利时", 14, ["Wout", "Jasper", "Tiesj", "Dries", "Lennert", "Stijn",
                          "Bert", "Kobe", "Arnaud", "Milan"],
            ["Vermeulen", "De Ridder", "Vanhoof", "Claeys", "Peeters", "Maes",
             "Coppens", "Vandael", "Roelandt", "Segers"]),
    "FRA": ("法国", 13, ["Rémi", "Julien", "Bastien", "Corentin", "Aurélien",
                        "Mathis", "Léo", "Valentin", "Hugo", "Clément"],
            ["Lefèvre", "Marchand", "Dubois", "Rouhier", "Vasseur", "Beaulieu",
             "Chapuis", "Delorme", "Fontaine", "Guérin"]),
    "ITA": ("意大利", 12, ["Matteo", "Lorenzo", "Alessio", "Davide", "Filippo",
                          "Giulio", "Andrea", "Nicolò", "Samuele", "Edoardo"],
            ["Fantini", "Bergamin", "Rossetti", "Marchetti", "Colombo", "Ferraro",
             "Sartori", "Bellini", "Grassi", "Moretti"]),
    "ESP": ("西班牙", 11, ["Íñigo", "Marc", "Álvaro", "Rubén", "Javier", "Unai",
                          "Sergio", "Adrián", "Pau", "Diego"],
            ["Aranguren", "Solana", "Ibáñez", "Castellón", "Herrero", "Vidal",
             "Casals", "Montoya", "Estévez", "Quintana"]),
    "NED": ("荷兰", 10, ["Daan", "Sven", "Bram", "Thijs", "Ruben", "Joost",
                        "Niels", "Koen", "Lars", "Mees"],
            ["van Dijk", "de Groot", "Bakker", "Visser", "van Leeuwen", "Kuipers",
             "Hoekstra", "Vermeer", "Dekker", "Smulders"]),
    "SLO": ("斯洛文尼亚", 6, ["Matej", "Žan", "Luka", "Rok", "Jaka", "Nejc",
                            "Tim", "Domen"],
            ["Kovačič", "Zupan", "Novak", "Hribar", "Petrič", "Golob",
             "Vidmar", "Kralj"]),
    "COL": ("哥伦比亚", 8, ["Santiago", "Camilo", "Juan", "Esteban", "Mauricio",
                          "Andrés", "Felipe", "Óscar"],
            ["Restrepo", "Muñoz", "Ospina", "Valderrama", "Cárdenas", "Arango",
             "Betancur", "Zapata"]),
    "DEN": ("丹麦", 7, ["Mikkel", "Kasper", "Anders", "Emil", "Frederik",
                       "Rasmus", "Jonas", "Magnus"],
            ["Sørensen", "Lund", "Bech", "Kristoffersen", "Holm", "Dahl",
             "Riis", "Storm"]),
    "GBR": ("英国", 7, ["Oliver", "Harry", "Callum", "Finn", "Elliot", "Josh",
                       "Reece", "Toby"],
            ["Whitfield", "Hargreaves", "Pemberton", "Ashcroft", "Radcliffe",
             "Sinclair", "Thornton", "Ellery"]),
    "GER": ("德国", 6, ["Jonas", "Nico", "Lukas", "Fabian", "Tobias", "Marcel",
                       "Silas", "Henrik"],
            ["Weissmann", "Brandt", "Kühne", "Heller", "Ostermann", "Bauer",
             "Reinhardt", "Sommer"]),
    "AUS": ("澳大利亚", 5, ["Jack", "Ryan", "Cooper", "Brodie", "Lachlan",
                          "Declan", "Hayden", "Zac"],
            ["Callaghan", "Prescott", "Whittaker", "Bowden", "Marsden",
             "Hollis", "Rankin", "Sturgess"]),
    "NOR": ("挪威", 4, ["Sondre", "Håkon", "Even", "Torstein", "Eirik", "Vegard"],
            ["Bjørnstad", "Halvorsen", "Nygård", "Solheim", "Rødland", "Aune"]),
    "SUI": ("瑞士", 4, ["Yannick", "Silvan", "Nino", "Robin", "Timon", "Gino"],
            ["Zumbrunn", "Bircher", "Steiner", "Rüegg", "Kaufmann", "Achermann"]),
    "POR": ("葡萄牙", 3, ["Rui", "Tiago", "Nuno", "Gonçalo", "Bruno", "Duarte"],
            ["Cardoso", "Almeida", "Teixeira", "Faria", "Moreira", "Baptista"]),
    "USA": ("美国", 4, ["Bryce", "Colton", "Garrett", "Tanner", "Wyatt", "Miles"],
            ["Hallman", "Rockwell", "Vaughn", "Sutter", "Larkin", "Brennan"]),
    "JPN": ("日本", 3, ["Haruto", "Sota", "Ren", "Yuma", "Kaito", "Riku"],
            ["Kurosawa", "Nakagawa", "Fujimoto", "Sakai", "Hoshino", "Miyata"]),
    "CHN": ("中国", 3, ["志远", "承宇", "亦骁", "泽楷", "俊驰", "临风"],
            ["林", "沈", "陆", "苏", "顾", "程"]),
    "ERI": ("厄立特里亚", 3, ["Biniam", "Natnael", "Merhawi", "Awet", "Henok"],
            ["Ghebre", "Tesfay", "Kidane", "Mekonnen", "Habte"]),
}

_CODES = list(NATIONS)
_WEIGHTS = [NATIONS[c][1] for c in _CODES]


def pick_nation(rng: random.Random) -> str:
    return rng.choices(_CODES, weights=_WEIGHTS, k=1)[0]


def make_name(nation: str, rng: random.Random) -> str:
    _, _, firsts, lasts = NATIONS[nation]
    first, last = rng.choice(firsts), rng.choice(lasts)
    # 中文姓名是姓在前，不加空格
    return f"{last}{first}" if nation == "CHN" else f"{first} {last}"


def nation_label(nation: str) -> str:
    return NATIONS[nation][0]
