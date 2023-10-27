from decimal import Decimal
from typing import Optional
from liquid.filter import liquid_filter, int_arg, with_context
from liquid import Context
import babel.numbers
import math

NUMBER_FORMAT = "#.###,##E+0"
BABEL_NUMBER_OPTIONS = {
    'default_format': NUMBER_FORMAT
}


@liquid_filter
@with_context
def dict2doku(obj: object, *, context: Context, max_heading: Optional[object] = None) -> str:
    """
    Recursively convert an object structure into a DokuWiki String.
    """
    heading = int_arg(max_heading) if max_heading else 4
    lines = ""
    complex_lines = ""
    d = obj if isinstance(obj, dict) else obj.__dict__
    for key in d:
        value = d[key]
        if is_primitve(value):
            lines += f"  * {key}: {format_data(value, context)}\n"
        elif is_qkit_property(value):
            lines += f"  * {key}: {format_data(value['content'], context)} (setter: {value['has_setter']})\n"
        else:
            complex_lines += format_heading(key, heading)
            complex_lines += dict2doku(value, context=context, max_heading=heading - 1)
    return lines + "\n" + complex_lines


def is_primitve(obj: any) -> bool:
    return not (hasattr(obj, '__dict__') or isinstance(obj, dict))


def is_qkit_property(obj: any) -> bool:
    """
    Detect if this object is a qkit property and can be simplified.
    """
    return isinstance(obj, dict) and set(obj.keys()) == {'content', 'has_setter'}


def format_heading(content, level):
    affix = "=" * level
    return " ".join([affix, content, affix]) + "\n"


def format_data(data, context):
    if isinstance(data, bool):
        return data
    if isinstance(data, (float, int, Decimal)) and not (math.isinf(data) or math.isnan(data)):
        number_format = context.resolve("number_format", NUMBER_FORMAT)
        locale = context.resolve("locale", "en_US")
        return babel.numbers.format_decimal(data, locale=locale, format=number_format)
    return data
