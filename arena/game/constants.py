"""Balance knobs for the placeholder game "Ore Rush" (v0).

These numbers are game design, not platform. Change them freely; bots can read them
through ``GameConstants`` and ``RobotType`` attributes.
"""

MAX_ROUNDS = 1000
MAP_MIN_SIZE = 24
MAP_MAX_SIZE = 36

# Economy
STARTING_ORE = 150
PASSIVE_INCOME = 2          # ore each team gets every round
MINE_AMOUNT = 2             # ore gained per successful mine() action
ORE_DEPOSIT_MIN = 40        # ore stored in one deposit tile when the map is generated
ORE_DEPOSIT_MAX = 120

# Cooldowns (Battlecode style): an action adds cooldown, every round removes
# COOLDOWN_PER_ROUND, and a robot may act while its cooldown is < COOLDOWN_THRESHOLD.
COOLDOWN_PER_ROUND = 10
COOLDOWN_THRESHOLD = 10

MAX_UNITS_PER_TEAM = 50     # living non-HQ robots per team

# Team-wide communication
SHARED_ARRAY_SIZE = 64
SHARED_ARRAY_MAX_VALUE = 65535

# Compute: instructions available to module-level code the first time a robot runs.
# Per-turn budgets are per robot type (see ROBOT_STATS).
INIT_BUDGET = 50_000

# Debug output limits (per robot per turn)
INDICATOR_STRING_MAX_LEN = 64
INDICATOR_MAX_DOTS = 16
INDICATOR_MAX_LINES = 16
LOG_MAX_CHARS_PER_TURN = 2000

ROBOT_STATS = {
    "HQ": dict(
        cost=0, health=1000, vision_radius_sq=34, action_radius_sq=13, damage=4,
        move_cooldown=0, action_cooldown=10, budget=20_000,
    ),
    "MINER": dict(
        cost=40, health=30, vision_radius_sq=20, action_radius_sq=2, damage=0,
        move_cooldown=10, action_cooldown=10, budget=10_000,
    ),
    "SOLDIER": dict(
        cost=60, health=50, vision_radius_sq=20, action_radius_sq=10, damage=6,
        move_cooldown=10, action_cooldown=10, budget=10_000,
    ),
}

# Fixed instruction cost charged for each RobotController call, on top of the
# instructions executed by the bot's own code.
API_COST = dict(
    info=1,          # get_* / is_* getters
    sense_one=5,     # single-tile sensing, can_sense_location, on_the_map
    sense_many=100,  # sense_nearby_robots / sense_nearby_ore
    check=5,         # can_move / can_attack / can_mine / can_build
    action=10,       # move / attack / mine / build
    shared_read=2,
    shared_write=5,
    indicator=5,
    log=10,          # each print() call
)
