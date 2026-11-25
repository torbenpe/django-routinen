from django import template

from ..core import get_tile

register = template.Library()


@register.inclusion_tag("routinen/tile.html")
def routinen_tile():
    """Die Kachel. Django reicht das CSRF-Token an Inclusion-Tags von selbst weiter."""
    return {"tile": get_tile()}
