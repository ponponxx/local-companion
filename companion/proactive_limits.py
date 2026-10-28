"""User-controlled daily quota shared by settings and both schedulers."""
def parse_limit(value):
    value = str(value).strip().lower()
    if value in {'unlimited', '不限', '不限制', '無上限'}:
        return 'unlimited'
    if value.isdecimal():
        return str(int(value))
    raise ValueError('請輸入每日則數（0 或正整數），或 unlimited／不限；0 表示不發主動聊天。')


def limit_label(value):
    return '不限制' if parse_limit(value) == 'unlimited' else str(value)


def limit_reached(value, count):
    limit = parse_limit(value)
    return limit != 'unlimited' and count >= int(limit)
