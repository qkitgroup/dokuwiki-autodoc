from typing import Optional
from liquid.filter import liquid_filter, int_arg


@liquid_filter
def dict2doku(obj: object, max_heading: Optional[object] = None) -> str:
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
            lines += f"  * {key}: {value}\n"
        elif is_qkit_property(value):
            lines += f"  * {key}: {value['content']} (setter: {value['has_setter']})\n"
        else:
            complex_lines += format_heading(key, heading)
            complex_lines += dict2doku(value, heading - 1)
    return lines + "\n" + complex_lines

def is_primitve(obj: any) -> bool:
    return not (hasattr(obj, '__dict__') or isinstance(obj, dict))

def is_qkit_property(obj: any) -> bool:
    """
    Detect if this object is a qkit property and can be simplified.
    """
    return isinstance(obj, dict) and set(obj.keys()) == set(('content', 'has_setter'))

def format_heading(content, level):
    affix = "=" * level
    return " ".join([affix, content, affix]) + "\n"