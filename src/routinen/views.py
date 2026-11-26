from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST, require_safe

from . import core

TEMPLATE = "routinen/tile.html"
PAGE_TEMPLATE = "routinen/verwaltung.html"
FORM_KEYS = ("name", "intervall", "toleranz", "zuletzt", "beschreibung")


@require_safe
def tile(request):
    context = core.get_tile(show_all=request.GET.get("all") == "1", edit=request.GET.get("edit") or None)
    return render(request, TEMPLATE, {"tile": context})


@require_safe
def verwaltung(request):
    context = core.get_overview(
        edit=request.GET.get("edit") or None,
        loeschen=request.GET.get("loeschen") or None,
        q=request.GET.get("q", ""),
    )
    return render(request, PAGE_TEMPLATE, {"verwaltung": context})


def _from_page(request):
    return request.POST.get("zurueck") == "verwaltung"


def _after_post(request, **kwargs):
    if _from_page(request):  # Verwaltungsseite: normale Formulare, zurück per Redirect (Post/Redirect/Get)
        return redirect("routinen:verwaltung")
    # Ohne HTMX (kein JavaScript) zurück zur Seite mit der Kachel statt eines nackten Partials
    if request.headers.get("HX-Request") != "true":
        return redirect(getattr(settings, "ROUTINEN_HOME_URL", "/"))
    context = core.get_tile(show_all=request.POST.get("all") == "1", **kwargs)
    return render(request, TEMPLATE, {"tile": context})


@require_POST
def done(request, routine_id):
    try:
        core.mark_done(routine_id)
    except KeyError:
        raise Http404 from None
    except core.StoreError:
        pass  # die Kachel zeigt den Fehler selbst
    return _after_post(request)


@require_POST
def save(request):
    routine_id = request.POST.get("id") or None
    fields, errors = core.parse_form(request.POST)
    if errors:
        form = {key: request.POST.get(key, "") for key in FORM_KEYS}
        if _from_page(request):
            context = core.get_overview(edit=routine_id or "neu", form=form, errors=errors)
            return render(request, PAGE_TEMPLATE, {"verwaltung": context})
        return _after_post(request, edit=routine_id or "neu", form=form, errors=errors)
    try:
        core.save_entry(routine_id, fields)
    except KeyError:
        raise Http404 from None
    except core.StoreError:
        pass
    return _after_post(request)


@require_POST
def delete(request, routine_id):
    try:
        core.delete_entry(routine_id)
    except KeyError:
        raise Http404 from None
    except core.StoreError:
        pass
    return _after_post(request)
