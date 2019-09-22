from django.shortcuts import render
from django.http import HttpResponseNotFound
from django.shortcuts import get_object_or_404
from django.db.models import Q, Exists, OuterRef
from el_pagination.decorators import page_template


from clist.models import Contest
from ranking.models import Statistics
from clist.templatetags.extras import slug
from clist.views import get_timezone, get_timeformat


@page_template('standings_list_paging.html')
def standings_list(request, template='standings_list.html', extra_context=None):
    contests = Contest.objects \
        .annotate(has_statistics=Exists(Statistics.objects.filter(contest=OuterRef('pk')))) \
        .filter(has_statistics=True) \
        .order_by('-end_time', 'title')
    search = request.GET.get('search')
    if search is not None:
        contests = contests.filter(Q(title__iregex=search) | Q(resource__host__iregex=search))

    context = {
        'contests': contests,
        'timezone': get_timezone(request),
        'timeformat': get_timeformat(request),
    }

    if extra_context is not None:
        context.update(extra_context)

    return render(request, template, context)


@page_template('standings_paging.html')
def standings(request, title_slug, contest_id, template='standings.html', extra_context=None):
    contest = get_object_or_404(Contest, pk=contest_id)
    if slug(contest.title) != title_slug:
        return HttpResponseNotFound(f'Not found {slug(contest.title)} slug')

    statistics = Statistics.objects.filter(contest=contest)

    statistics = statistics \
        .extra(select={'place_as_int': "CAST(SPLIT_PART(place, '-', 1) AS INTEGER)"}) \
        .select_related('account') \
        .order_by('place_as_int', '-solving', '-upsolving')

    params = {}
    if 'division' in contest.info.get('problems', {}):
        division = request.GET.get(
            'division',
            sorted(contest.info['problems']['division'].keys())[0],
        )
        params['division'] = division
        statistics = statistics.filter(addition__division=division)

    search = request.GET.get('search')
    if search:
        statistics = statistics.filter(Q(account__key__iregex=search) | Q(addition__name__iregex=search))
        statistics = statistics.extra(select={'row_num': 'ROW_NUMBER() OVER (ORDER BY "ranking_statistics"."id")'})

    fields = []
    for k, v in (
        ('penalty', 'penalty'),
        ('total_time', 'time'),
    ):
        if k in contest.info.get('fields', []):
            fields.append((k, v))

    context = {
        'contest': contest,
        'statistics': statistics,
        'params': params,
        'fields': fields,
        'with_row_num': bool(search),
    }

    if extra_context is not None:
        context.update(extra_context)

    return render(request, template, context)
