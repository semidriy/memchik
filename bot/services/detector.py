import hashlib
from aiogram.types import User as TgUser

MALE_NAMES = {
    "александр", "алексей", "андрей", "антон", "артём", "артем", "борис",
    "вадим", "василий", "виктор", "виталий", "владимир", "владислав",
    "вячеслав", "григорий", "даниил", "данил", "денис", "дмитрий",
    "евгений", "иван", "игорь", "илья", "кирилл", "константин",
    "леонид", "максим", "михаил", "никита", "николай", "олег",
    "павел", "пётр", "петр", "роман", "руслан", "сергей",
    "степан", "тимур", "фёдор", "федор", "филипп", "юрий", "яков",
    "арсений", "богдан", "вениамин", "георгий", "глеб", "егор",
    "захар", "лев", "мирон", "платон", "савелий", "тимофей", "эдуард",
    "alex", "alexey", "andrey", "anton", "artem", "boris", "daniil",
    "denis", "dmitry", "evgeny", "ivan", "igor", "ilya", "kirill",
    "maksim", "maxim", "mikhail", "nikita", "nikolay", "oleg",
    "pavel", "roman", "ruslan", "sergey", "timur", "yuri",
}

FEMALE_NAMES = {
    "александра", "алина", "алиса", "анастасия", "анна", "валентина",
    "валерия", "вера", "виктория", "галина", "дарья", "диана",
    "екатерина", "елена", "жанна", "зинаида", "зоя", "инна",
    "ирина", "карина", "кристина", "ксения", "лариса", "людмила",
    "маргарита", "марина", "мария", "надежда", "наталья", "наталия",
    "нина", "оксана", "ольга", "полина", "светлана", "снежана",
    "софья", "софия", "тамара", "татьяна", "юлия", "яна",
    "ангелина", "вероника", "дарина", "евгения", "елизавета",
    "камилла", "милана", "миляуша", "регина", "руслана",
    "anna", "alina", "anastasia", "darya", "diana", "elena",
    "irina", "katya", "ksenia", "maria", "natalia", "olga",
    "polina", "svetlana", "tatyana", "victoria", "yulia", "yana",
}


def guess_gender(first_name: str | None) -> str:
    if not first_name:
        return "U"
    name = first_name.lower().strip().split()[0]
    if name in MALE_NAMES:
        return "M"
    if name in FEMALE_NAMES:
        return "F"
    if name.endswith(("а", "я", "ия", "ья")):
        return "F"
    if name.endswith(("ий", "ей", "ой", "ев", "ов", "ин")):
        return "M"
    return "U"


def is_suspicious_user(tg_user: TgUser, previous_visits_count: int) -> bool:
    if tg_user.is_bot:
        return True
    if previous_visits_count >= 3:
        return True
    if not tg_user.first_name and not tg_user.username:
        return True
    return False


def hash_text(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()
