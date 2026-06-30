from aiogram.fsm.state import State, StatesGroup


class BroadcastStates(StatesGroup):
    waiting_content = State()
    editing_content = State()
    confirm = State()
    waiting_buttons = State()


class LinkStates(StatesGroup):
    waiting_name = State()


class OpStates(StatesGroup):
    waiting_input = State()    # 5-line add format
    waiting_rename = State()


class TemplateStates(StatesGroup):
    waiting_media = State()
    waiting_title = State()
    waiting_tags = State()
    editing_tags = State()


class MemeStates(StatesGroup):
    waiting_template = State()
    waiting_text = State()


class UserTemplateStates(StatesGroup):
    choosing_type = State()
    waiting_media = State()
    waiting_tags = State()
    waiting_title = State()


class ModerationStates(StatesGroup):
    waiting_reject_comment = State()


class PlacementStates(StatesGroup):
    waiting_name = State()
    waiting_caption = State()
    waiting_button = State()
    waiting_limit_count = State()
    waiting_limit_window = State()
    waiting_duration = State()
    waiting_duration_manual = State()


class DisplayStates(StatesGroup):
    waiting_name = State()
    waiting_media = State()
    waiting_caption = State()
    waiting_button = State()
    waiting_sort_order = State()


class AdminMgmtStates(StatesGroup):
    waiting_user = State()


class LanguageStates(StatesGroup):
    waiting_new_input = State()       # one line: "code|name|flag"
    waiting_translation = State()     # editing single translation key
    waiting_buttons = State()         # editing bot_buttons rows for lang
