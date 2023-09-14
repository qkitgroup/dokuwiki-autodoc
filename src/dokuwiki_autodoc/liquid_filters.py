from typing import Optional
from liquid.filter import liquid_filter, int_arg


@liquid_filter
def dict2doku(obj: object, max_heading: Optional[object] = None) -> str:
    heading = int_arg(max_heading) if max_heading else 4
    lines = ""
    complex_lines = ""
    d = obj if isinstance(obj, dict) else obj.__dict__
    for key in d:
        value = d[key]
        if is_primitve(value):
            lines += f"  * {key}: {value}\n"
        else:
            complex_lines += format_heading(key, heading)
            complex_lines += dict2doku(value, heading - 1)
    return lines + "\n" + complex_lines

def is_primitve(obj: any) -> bool:
    return not (hasattr(obj, '__dict__') or isinstance(obj, dict))


def format_heading(content, level):
    affix = "=" * level
    return " ".join([affix, content, affix]) + "\n"