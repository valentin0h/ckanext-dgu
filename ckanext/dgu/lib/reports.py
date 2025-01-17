import collections
import datetime
import logging
import os

from ckan import model
from ckan.lib.helpers import OrderedDict
import ckan.plugins as p
from ckanext.report import lib
from ckanext.dgu.lib.publisher import go_up_tree
from ckanext.dgu.lib import helpers as dgu_helpers

log = logging.getLogger(__name__)


# NII


def nii_report():
    '''A list of the NII datasets, grouped by publisher, with details of broken
    links and source.'''
    nii_dataset_q = model.Session.query(model.Package)\
        .join(model.PackageExtra, model.PackageExtra.package_id == model.Package.id)\
        .join(model.Group, model.Package.owner_org == model.Group.id)\
        .filter(model.PackageExtra.key == 'core-dataset')\
        .filter(model.PackageExtra.value == 'true')\
        .filter(model.Package.state == 'active')
    nii_dataset_objects = nii_dataset_q\
            .order_by(model.Group.title, model.Package.title).all()

    def broken_resources_for_package(package_id):
        from ckanext.archiver.model import Archival

        results = model.Session.query(Archival, model.Resource)\
                       .filter(Archival.package_id == package_id)\
                       .filter(Archival.is_broken == True)\
                       .join(model.Package, Archival.package_id == model.Package.id)\
                       .filter(model.Package.state == 'active')\
                       .join(model.Resource, Archival.resource_id == model.Resource.id)\
                       .filter(model.Resource.state == 'active')

        broken_resources = [(resource.description, resource.id)
                            for archival, resource in results.all()]
        return broken_resources

    nii_dataset_details = []
    num_resources = 0
    num_broken_resources = 0
    num_broken_datasets = 0
    broken_organization_names = set()
    nii_organizations = set()
    for dataset_object in nii_dataset_objects:
        broken_resources = broken_resources_for_package(dataset_object.id)
        org = dataset_object.get_organization()
        dataset_details = {
                'name': dataset_object.name,
                'title': dataset_object.title,
                'dataset_notes': lib.dataset_notes(dataset_object),
                'organization_name': org.name,
                'unpublished': p.toolkit.asbool(dataset_object.extras.get('unpublished')),
                'num_broken_resources': len(broken_resources),
                'broken_resources': broken_resources,
                }
        nii_dataset_details.append(dataset_details)
        if broken_resources:
            num_broken_resources += len(broken_resources)
            num_broken_datasets += 1
            broken_organization_names.add(org.name)
        nii_organizations.add(org)
        num_resources += len(dataset_object.resources)

    org_tuples = [(org.name, org.title) for org in
                  sorted(nii_organizations, key=lambda o: o.title)]

    return {'table': nii_dataset_details,
            'organizations': org_tuples,
            'num_resources': num_resources,
            'num_datasets': len(nii_dataset_objects),
            'num_organizations': len(nii_organizations),
            'num_broken_resources': num_broken_resources,
            'num_broken_datasets': num_broken_datasets,
            'num_broken_organizations': len(broken_organization_names),
            }

nii_report_info = {
    'name': 'nii',
    'title': 'National Information Infrastructure',
    'description': 'Details of the datasets in the NII.',
    'option_defaults': OrderedDict([]),
    'option_combinations': None,
    'generate': nii_report,
    'template': 'report/nii.html',
}


# Publisher resources


def publisher_resources(organization=None,
                        include_sub_organizations=False):
    '''
    Returns a dictionary detailing resources for each dataset in the
    organisation specified.
    '''
    org = model.Group.by_name(organization)
    if not org:
        raise p.toolkit.ObjectNotFound('Publisher not found')

    # Get packages
    pkgs = model.Session.query(model.Package)\
                .filter_by(state='active')
    pkgs = lib.filter_by_organizations(pkgs, organization,
                                       include_sub_organizations).all()

    # Get their resources
    def create_row(pkg_, resource_dict):
        org_ = pkg_.get_organization()
        return OrderedDict((
                ('publisher_title', org_.title),
                ('publisher_name', org_.name),
                ('package_title', pkg_.title),
                ('package_name', pkg_.name),
                ('package_notes', lib.dataset_notes(pkg_)),
                ('resource_position', resource_dict.get('position')),
                ('resource_id', resource_dict.get('id')),
                ('resource_description', resource_dict.get('description')),
                ('resource_url', resource_dict.get('url')),
                ('resource_format', resource_dict.get('format')),
                ('resource_created', resource_dict.get('created')),
               ))
    num_resources = 0
    rows = []
    for pkg in pkgs:
        resources = pkg.resources
        if resources:
            for res in resources:
                res_dict = {'id': res.id, 'position': res.position,
                            'description': res.description, 'url': res.url,
                            'format': res.format,
                            'created': (res.created.isoformat()
                                        if res.created else None)}
                rows.append(create_row(pkg, res_dict))
            num_resources += len(resources)
        else:
            # packages with no resources are still listed
            rows.append(create_row(pkg, {}))

    return {'organization_name': org.name,
            'organization_title': org.title,
            'num_datasets': len(pkgs),
            'num_resources': num_resources,
            'table': rows,
            }

def publisher_resources_combinations():
    for organization in lib.all_organizations():
        for include_sub_organizations in (False, True):
                yield {'organization': organization,
                       'include_sub_organizations': include_sub_organizations}

publisher_resources_info = {
    'name': 'publisher-resources',
    'description': 'A list of all the datasets and resources for a publisher.',
    'option_defaults': OrderedDict((('organization', 'cabinet-office'),
                                    ('include_sub_organizations', False))),
    'option_combinations': publisher_resources_combinations,
    'generate': publisher_resources,
    'template': 'report/publisher_resources.html',
    }


def get_quarter_dates(datetime_now):
    '''Returns the dates for this (current) quarter and last quarter. Uses
    calendar year, so 1 Jan to 31 Mar etc.'''
    now = datetime_now
    month_this_q_started = (now.month - 1) // 3 * 3 + 1
    this_q_started = datetime.datetime(now.year, month_this_q_started, 1)
    this_q_ended = datetime.datetime(now.year, now.month, now.day)
    last_q_started = datetime.datetime(
                      this_q_started.year + (this_q_started.month-3)/12,
                      (this_q_started.month-4) % 12 + 1,
                      1)
    last_q_ended = this_q_started - datetime.timedelta(days=1)
    return {'this': (this_q_started, this_q_ended),
            'last': (last_q_started, last_q_ended)}


def publisher_activity(organization, include_sub_organizations=False):
    """
    Contains information about the datasets a specific organization has
    released in this and last quarter (calendar year). This is needed by
    departments for their quarterly transparency reports.
    """
    import datetime
    import ckan.model as model
    from paste.deploy.converters import asbool

    # These are the authors whose revisions we ignore, as they are trivial
    # changes. NB we do want to know about revisions by:
    # * harvest (harvested metadata)
    # * dgu (NS Stat Hub imports)
    # * Fix national indicators
    system_authors = ('autotheme', 'co-prod3.dh.bytemark.co.uk',
                      'Date format tidier', 'current_revision_fixer',
                      'current_revision_fixer2', 'fix_contact_details.py',
                      'Repoint 410 Gone to webarchive url',
                      'Fix duplicate resources',
                      'fix_secondary_theme.py',
                      )
    system_author_template = 'script-%'  # "%" is a wildcard

    created = {'this': [], 'last': []}
    modified = {'this': [], 'last': []}

    now = datetime.datetime.now()
    quarters = get_quarter_dates(now)

    if organization:
        organization = model.Group.by_name(organization)
        if not organization:
            raise p.toolkit.ObjectNotFound()

    if not organization:
        pkgs = model.Session.query(model.Package)\
                .all()
    else:
        pkgs = model.Session.query(model.Package)
        pkgs = lib.filter_by_organizations(pkgs, organization,
                                           include_sub_organizations).all()

    for pkg in pkgs:
        created_ = model.Session.query(model.PackageRevision)\
            .filter(model.PackageRevision.id == pkg.id) \
            .order_by("revision_timestamp asc").first()

        pr_q = model.Session.query(model.PackageRevision, model.Revision)\
            .filter(model.PackageRevision.id == pkg.id)\
            .filter_by(state='active')\
            .join(model.Revision)\
            .filter(~model.Revision.author.in_(system_authors)) \
            .filter(~model.Revision.author.like(system_author_template))
        rr_q = model.Session.query(model.Package, model.ResourceRevision, model.Revision)\
            .filter(model.Package.id == pkg.id)\
            .filter_by(state='active')\
            .join(model.ResourceGroup)\
            .join(model.ResourceRevision,
                  model.ResourceGroup.id == model.ResourceRevision.resource_group_id)\
            .join(model.Revision)\
            .filter(~model.Revision.author.in_(system_authors))\
            .filter(~model.Revision.author.like(system_author_template))
        pe_q = model.Session.query(model.Package, model.PackageExtraRevision, model.Revision)\
            .filter(model.Package.id == pkg.id)\
            .filter_by(state='active')\
            .join(model.PackageExtraRevision,
                  model.Package.id == model.PackageExtraRevision.package_id)\
            .join(model.Revision)\
            .filter(~model.Revision.author.in_(system_authors))\
            .filter(~model.Revision.author.like(system_author_template))

        for quarter_name in quarters:
            quarter = quarters[quarter_name]
            # created
            if quarter[0] < created_.revision_timestamp < quarter[1]:
                published = not asbool(pkg.extras.get('unpublished'))
                created[quarter_name].append(
                    (created_.name, created_.title, lib.dataset_notes(pkg),
                     'created', quarter_name,
                     created_.revision_timestamp.isoformat(),
                     created_.revision.author, published))

            # modified
            # exclude the creation revision
            period_start = max(quarter[0], created_.revision_timestamp)
            prs = pr_q.filter(model.PackageRevision.revision_timestamp > period_start)\
                        .filter(model.PackageRevision.revision_timestamp < quarter[1])
            rrs = rr_q.filter(model.ResourceRevision.revision_timestamp > period_start)\
                        .filter(model.ResourceRevision.revision_timestamp < quarter[1])
            pes = pe_q.filter(model.PackageExtraRevision.revision_timestamp > period_start)\
                        .filter(model.PackageExtraRevision.revision_timestamp < quarter[1])
            authors = ' '.join(set([r[1].author for r in prs] +
                                   [r[2].author for r in rrs] +
                                   [r[2].author for r in pes]))
            dates = set([r[1].timestamp.date() for r in prs] +
                        [r[2].timestamp.date() for r in rrs] +
                        [r[2].timestamp.date() for r in pes])
            dates_formatted = ' '.join([date.isoformat()
                                        for date in sorted(dates)])
            if authors:
                published = not asbool(pkg.extras.get('unpublished'))
                modified[quarter_name].append(
                    (pkg.name, pkg.title, lib.dataset_notes(pkg),
                        'modified', quarter_name,
                        dates_formatted, authors, published))

    datasets = []
    for quarter_name in quarters:
        datasets += sorted(created[quarter_name], key=lambda x: x[1])
        datasets += sorted(modified[quarter_name], key=lambda x: x[1])
    columns = ('Dataset name', 'Dataset title', 'Dataset notes', 'Modified or created', 'Quarter', 'Timestamp', 'Author', 'Published')

    quarters_iso = dict([(last_or_this, [date_.isoformat() for date_ in q_list])
                         for last_or_this, q_list in quarters.iteritems()])

    return {'table': datasets, 'columns': columns,
            'quarters': quarters_iso}

def publisher_activity_combinations():
    for org in lib.all_organizations(include_none=False):
        for include_sub_organizations in (False, True):
            yield {'organization': org,
                   'include_sub_organizations': include_sub_organizations}

publisher_activity_report_info = {
    'name': 'publisher-activity',
    'description': 'A quarterly list of datasets created and edited by a publisher.',
    'option_defaults': OrderedDict((('organization', 'cabinet-office'),
                                    ('include_sub_organizations', False),
                                    )),
    'option_combinations': publisher_activity_combinations,
    'generate': publisher_activity,
    'template': 'report/publisher_activity.html',
    }


def unpublished():
    pkgs = model.Session.query(model.Package)\
                .filter_by(state='active')\
                .join(model.PackageExtra)\
                .filter_by(key='unpublished')\
                .filter_by(value='true')\
                .filter_by(state='active')\
                .all()
    pkg_dicts = []
    for pkg in pkgs:
        org = pkg.get_organization()
        pkg_dict = {
                'name': pkg.name,
                'title': pkg.title,
                'organization title': org.title,
                'organization name': org.name,
                'notes': pkg.notes,
                'publish date': pkg.extras.get('publish-date'),
                'will not be released': pkg.extras.get('publish-restricted'),
                'release notes': pkg.extras.get('release-notes'),
                }
        pkg_dicts.append(pkg_dict)
    return {'table': pkg_dicts}

unpublished_report_info = {
    'name': 'unpublished',
    'title': 'Unpublished datasets',
    'description': 'Unpublished dataset properties provided by publishers.',
    'option_defaults': None,
    'option_combinations': None,
    'generate': unpublished,
    'template': 'report/unpublished.html',
    }

def last_resource_deleted(pkg):

    resource_revisions = model.Session.query(model.ResourceRevision) \
                              .join(model.ResourceGroup) \
                              .join(model.Package) \
                              .filter_by(id=pkg.id) \
                              .order_by(model.ResourceRevision.revision_timestamp) \
                              .all()
    previous_rr = None
    # go through the RRs in reverse chronological order and when an active
    # revision is found, return the rr in the previous loop.
    for rr in resource_revisions[::-1]:
        if rr.state == 'active':
            return previous_rr.revision_timestamp, previous_rr.url
        previous_rr = rr
    return None, ''

def datasets_without_resources():
    pkg_dicts = []
    pkgs = model.Session.query(model.Package)\
                .filter_by(state='active')\
                .order_by(model.Package.title)\
                .all()
    for pkg in pkgs:
        if len(pkg.resources) != 0 or \
          pkg.extras.get('unpublished', '').lower() == 'true':
            continue
        org = pkg.get_organization()
        deleted, url = last_resource_deleted(pkg)
        pkg_dict = OrderedDict((
                ('name', pkg.name),
                ('title', pkg.title),
                ('organization title', org.title),
                ('organization name', org.name),
                ('metadata created', pkg.metadata_created.isoformat()),
                ('metadata modified', pkg.metadata_modified.isoformat()),
                ('last resource deleted', deleted.isoformat() if deleted else None),
                ('last resource url', url),
                ('dataset_notes', lib.dataset_notes(pkg)),
                ))
        pkg_dicts.append(pkg_dict)
    return {'table': pkg_dicts}


datasets_without_resources_info = {
    'name': 'datasets-without-resources',
    'title': 'Datasets without resources',
    'description': 'Datasets that have no resources (data URLs). Excludes unpublished ones.',
    'option_defaults': None,
    'option_combinations': None,
    'generate': datasets_without_resources,
    'template': 'report/datasets_without_resources.html',
    }

# app-dataset

def app_dataset_report():
    app_dataset_dicts = []
    for related in model.Session.query(model.RelatedDataset) \
                        .filter(model.Related.type=='App') \
                        .all():
        dataset = related.dataset
        org = dataset.get_organization()
        top_org = list(go_up_tree(org))[-1]

        app_dataset_dict = OrderedDict((
            ('app title', related.related.title),
            ('app url', related.related.url),
            ('dataset name', dataset.name),
            ('dataset title', dataset.title),
            ('organization title', org.title),
            ('organization name', org.name),
            ('top-level organization title', top_org.title),
            ('top-level organization name', top_org.name),
            ('dataset theme', related.dataset.extras.get('theme-primary', '')),
            ('dataset notes', lib.dataset_notes(dataset)),
            ))
        app_dataset_dicts.append(app_dataset_dict)

    app_dataset_dicts.sort(key=lambda row: row['top-level organization title']
                           + row['organization title'])

    return {'table': app_dataset_dicts}

app_dataset_report_info = {
    'name': 'app-dataset-report',
    'title': 'Apps with datasets',
    'description': 'Datasets that have been used by apps.',
    'option_defaults': None,
    'option_combinations': None,
    'generate': app_dataset_report,
    'template': 'report/app_dataset.html',
    }

# app-dataset by theme

def app_dataset_theme_report():
    table = []

    datasets = collections.defaultdict(lambda: {'apps': []})
    for related in model.Session.query(model.RelatedDataset).filter(model.Related.type=='App').all():
        dataset_name = related.dataset.name

        app = {
          'title': related.related.title,
          'url': related.related.url
        }

        datasets[dataset_name]['title'] = related.dataset.title
        datasets[dataset_name]['theme'] = related.dataset.extras.get('theme-primary', '')
        datasets[dataset_name]['apps'].append(app)

    for dataset_name, dataset in datasets.items():
        sorted_apps = sorted(dataset['apps'], key=lambda x: x['title'])
        table.append({'dataset_title': dataset['title'],
                      'dataset_name': dataset_name,
                      'theme': dataset['theme'],
                      'app_titles': "\n".join(a['title'] for a in sorted_apps),
                      'app_urls': "\n".join(a['url'] for a in sorted_apps)})

    return {'table': table}

app_dataset_theme_report_info = {
    'name': 'app-dataset-theme-report',
    'title': 'Apps with datasets by theme',
    'description': 'Datasets that have been used by apps, grouped by theme.',
    'option_defaults': None,
    'option_combinations': None,
    'generate': app_dataset_theme_report,
    'template': 'report/app_dataset_theme_report.html',
    }

# admin-editor report

def get_user_realname(user):
    from ckanext.dgu.drupalclient import DrupalClient
    from HTMLParser import HTMLParser

    if user.name.startswith('user_d'):
        user_id = user.name[len('user_d'):]

        html_parser = HTMLParser()

        try:
            dc = DrupalClient()
            properties = dc.get_user_properties(user_id)
        except Exception, ex:
            return user.fullname

        try:
            first_name = properties['field_first_name']['und'][0]['safe_value']
            first_name = html_parser.unescape(first_name)
        except:
            first_name = ''

        try:
            surname = properties['field_surname']['und'][0]['safe_value']
            surname = html_parser.unescape(surname)
        except:
            surname = ''
    else:
        first_name = ''
        surname = ''

    name = '%s %s' % (first_name, surname)
    if name.strip() == '':
        name = user.fullname

    return name

def admin_editor(org=None, include_sub_organizations=False):
    from ckanext.dgu.lib.helpers import group_get_users

    table = []

    if org:
        q = model.Group.all('organization')
        parent = model.Group.by_name(org)
        if not parent:
            raise p.toolkit.ObjectNotFound('Publisher not found')

        if include_sub_organizations:
            child_ids = [ch[0] for ch in parent.get_children_group_hierarchy(type='organization')]
        else:
            child_ids = []

        q = q.filter(model.Group.id.in_([parent.id] + child_ids))

        for g in q.all():
            record = {}
            record['publisher_name'] = g.name
            record['publisher_title'] = g.title

            admin_users = group_get_users(g, capacity='admin')
            admins = []
            for u in admin_users:
                name = get_user_realname(u)
                admins.append('%s <%s>' % (name, u.email))

            record['admins'] = "\n".join(admins)

            editor_users = group_get_users(g, capacity='editor')
            editors = []
            for u in editor_users:
                name = get_user_realname(u)
                editors.append('%s <%s>' % (name, u.email))

            record['editors'] = "\n".join(editors)
            table.append(record)
    else:
        table.append({})

    return {'table': table}

def admin_editor_combinations():
    from ckanext.dgu.lib.helpers import organization_list

    for org, _ in organization_list(top=False):
        for include_sub_organizations in (False, True):
            yield {'org': org,
                    'include_sub_organizations': include_sub_organizations}

def user_is_admin(user, org=None):
    import ckan.lib.helpers as helpers
    if org:
        return helpers.check_access('organization_update', {'id': org.id})
    else:
        # Are they admin of any org?
        return len(user.get_groups('organization', capacity='admin')) > 0

def user_is_rm(user, org=None):
    from pylons import config
    from ast import literal_eval
    from ckanext.dgu.lib.publisher import go_up_tree

    relationship_managers = literal_eval(config.get('dgu.relationship_managers', '{}'))

    allowed_orgs = relationship_managers.get(user.name, [])

    if org:
        for o in go_up_tree(org):
            if o.name in allowed_orgs:
                return True

        return False
    else:
        # Are they RM of any org?
        return len(allowed_orgs) > 0

def admin_editor_authorize(user, options):
    if not user:
        return False

    if user.sysadmin:
        return True

    if options.get('org', False):
        org_name = options["org"]
        org = model.Session.query(model.Group) \
                   .filter_by(name=org_name) \
                   .first()
        if not org:
            return False

        if user_is_admin(user, org) or user_is_rm(user, org):
            return True
        else:
            return False
    else:
        # Allow them to see front page / see report on report index
        if user_is_admin(user) or user_is_rm(user):
            return True

    return False

admin_editor_info = {
    'name': 'admin_editor',
    'title': 'Publisher administrators and editors',
    'description': 'Filterable list of publishers which shows who has administrator and editor rights.',
    'option_defaults': OrderedDict((('org', ''), ('include_sub_organizations', False))),
    'option_combinations': admin_editor_combinations,
    'generate': admin_editor,
    'template': 'report/admin_editor.html',
    'authorize': admin_editor_authorize
    }


# LA Schemas


def la_schemas(local_authority=None, schema=None, incentive_only=False):
    from ckanext.dgu.bin.schema_apply_lga import LaSchemas
    Options = collections.namedtuple('Options', ('organization', 'incentive_only', 'schema', 'write', 'dataset', 'print_'))
    options = Options(organization=None, incentive_only=incentive_only,
                      schema=schema, write=False, dataset=None, print_=False)
    csv_filepath = os.path.abspath(os.path.join(__file__, '../../incentive.csv'))
    return LaSchemas.command(config_ini=None, options=options,
                             submissions_csv_filepath=csv_filepath)


def la_schemas_combinations():
    for schema in [''] + dgu_helpers.get_la_schema_options():
        for incentive_only in (False, True):
            yield {'schema': schema['title'] if schema else '',
                   'incentive_only': incentive_only}

la_schemas_info = {
    'name': 'la-schemas',
    'title': 'Schemas for local authorities',
    'description': 'Schemas matched to local authority datasets.',
    'option_defaults': OrderedDict((('schema', ''),
                                    ('incentive_only', False))),
    'option_combinations': la_schemas_combinations,
    'generate': la_schemas,
    'template': 'report/la_schemas.html',
    }
