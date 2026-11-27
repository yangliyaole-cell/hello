"""Balance knobs for the territory game "Frontier".

Time: a match is MAX_ROUNDS rounds. In every round team A takes a turn, then team B.
Every "per round" number (income, upkeep, occupation time, spawn limit, ruin decay)
is counted in rounds, and a team's own numbers update at the end of its own turn.
Fights are the exception: they are resolved at the end of *every* turn.
"""

MAX_ROUNDS = 2000
MAP_WIDTH = 50
MAP_HEIGHT = 50

# Economy
STARTING_GOLD = 1000
SOLDIER_COST = 2               # gold per soldier
TILE_INCOME = 1                # gold per owned tile per round (the HQ tile counts)
RICH_TILE_INCOME = 5           # gold per owned resource-rich tile per round
UPKEEP_SOLDIERS_PER_GOLD = 10  # upkeep: 1 gold per 10 soldiers per round, rounded up (HQ garrison is free)

# HQ
HQ_GARRISON = 10               # free, immobile defenders on the HQ tile
HQ_DEFENSE_MULTIPLIER = 5      # one defender on its own HQ tile is worth this many attackers

# Squads
MAX_SPAWNS_PER_ROUND = 5       # new squads the HQ may create per round
# squad limit = number of owned tiles, not counting the HQ tile

# Occupation: rounds = ceil(BASE / total_gold), clamped to [1, MAX_ROUNDS_*]
# total_gold = soldiers_on_tile * gold_per_soldier, gold_per_soldier in [1, MAX_GOLD_PER_SOLDIER]
CLAIM_BASE = 200               # blank tile: 20 gold -> 10 rounds
CLAIM_MAX_ROUNDS = 100
REPAIR_BASE = 100              # ruined tile: 10 gold -> 10 rounds
REPAIR_MAX_ROUNDS = 50
MAX_GOLD_PER_SOLDIER = 10
RUIN_ROUNDS = 20               # a ruined tile turns blank after this many rounds unless it is being repaired

# Combat: each resolution, each side loses ceil(COMBAT_FACTOR * sqrt(enemy strength)),
# where defenders on their own HQ tile count HQ_DEFENSE_MULTIPLIER times and take 1/5 of the damage.
COMBAT_FACTOR = 0.5

# Vision: squads see VISION_RADIUS tiles in every direction (a 5x5 square), blocked by
# enemy territory. Owned territory also sees VISION_RADIUS tiles around itself.
VISION_RADIUS = 2

# Map
RICH_CLUSTER_SIZE = (7, 8)
RICH_CLUSTERS = (2, 3)

# Communication
BOARD_SIZE = 128               # team board slots, readable/writable only from the HQ network
BOARD_MIN_VALUE = -(2 ** 31)
BOARD_MAX_VALUE = 2 ** 31 - 1
MEMO_MAX_ITEMS = 256           # a memo is plain data: at most this many values in total
MEMO_MAX_CHARS = 2000          # ... and this many characters of strings in total

# Compute (instructions per turn)
SQUAD_BUDGET = 10_000
HQ_BUDGET = 30_000
INIT_BUDGET = 50_000           # module-level code, once per squad/HQ program instance

# Debug output limits (per agent per turn)
INDICATOR_STRING_MAX_LEN = 64
INDICATOR_MAX_DOTS = 16
INDICATOR_MAX_LINES = 16
LOG_MAX_CHARS_PER_TURN = 1000

# Fixed instruction cost of RobotController calls (variable-size results add more)
API_COST = dict(
    info=1,
    see=2,
    sense_one=5,
    sense_nearby=20,
    sense_network=20,
    check=5,
    action=10,
    memo=5,
    board_read=2,
    board_write=5,
    indicator=5,
    log=10,
)
