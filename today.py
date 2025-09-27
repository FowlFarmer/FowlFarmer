import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
# Issues and pull requests permissions not needed at the moment, but may be used in the future
HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME'] # 'FowlFarmer'
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def daily_readme(birthday):
    """
    Returns the length of time since I was born
    e.g. 'XX years, XX months, XX days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years), 
        diff.months, 'month' + format_plural(diff.months), 
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')


def format_plural(unit):
    """
    Returns a properly formatted number
    e.g.
    'day' + format_plural(diff.days) == 5
    >>> '5 days'
    'day' + format_plural(diff.days) == 1
    >>> '1 day'
    """
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_commits(start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = simple_request(graph_commits.__name__, query, variables)
    return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = simple_request(recursive_loc.__name__, query, variables)
    if request.json()['data']['repository']['defaultBranchRef'] != None: # Only count commits if repo isn't empty
        return loc_counter_one_repo(owner, repo_name, request.json()['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total, my_commits)
    else: 
        return 0, 0, 0


def loc_counter_one_repo(owner, repo_name, history, addition_total, deletion_total, my_commits):
    """
    Recursively call recursive_loc (since GraphQL can only search 100 commits at a time) 
    only adds the LOC value of commits authored by me
    """
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    else: return recursive_loc(owner, repo_name, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, cursor=None, edges=[]):
    """
    Uses GitHub's GraphQL v4 API to query all the repositories I have access to (with respect to owner_affiliation)
    Queries 60 repos at a time, because larger queries give a 502 timeout error and smaller queries send too many
    requests and also give a 502 error.
    Returns the total number of lines of code in all repositories
    """
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    if request.json()['data']['user']['repositories']['pageInfo']['hasNextPage']:   # If repository data has another page
        edges += request.json()['data']['user']['repositories']['edges']            # Add on to the LoC count
        return loc_query(owner_affiliation, request.json()['data']['user']['repositories']['pageInfo']['endCursor'], edges)
    else:
        # Calculate LOC directly for all repositories
        all_edges = edges + request.json()['data']['user']['repositories']['edges']
        loc_add, loc_del, my_commits = 0, 0, 0
        for edge in all_edges:
            if edge['node']['defaultBranchRef'] is not None:  # Only process non-empty repos
                owner, repo_name = edge['node']['nameWithOwner'].split('/')
                add, delete, commits = recursive_loc(owner, repo_name)
                loc_add += add
                loc_del += delete
                my_commits += commits
        return [loc_add, loc_del, loc_add - loc_del, my_commits]













def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """
    Parse SVG files and update elements with my age, commits, stars, repositories, and lines written
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'commit_data', commit_data, 24)
    justify_format(root, 'star_data', star_data, 12)
    # justify_format(root, 'repo_data', repo_data, 6)
    justify_format(root, 'contrib_data', contrib_data, 5) # Only show total contributed repos
    justify_format(root, 'follower_data', follower_data, 8)

    # New: handle the whole LOC display as one block
    loc_add, loc_del, loc_total = loc_data   # (additions, deletions, total)
    flat_text = build_loc_flat_text(loc_total, loc_add, loc_del)  # e.g. "446,276 ( 523,178++, 76,902-- )"
    update_loc_block(root, loc_total, loc_add, loc_del)           # injects colored tspans
    justify_dots(root, 'loc_block_dots', flat_text, target_len=42)


    justify_format(root, 'age_data', age_data, 49)  # added since your SVG has age_data
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """
    Updates and formats the text of the element, and modifies the amount of dots
    in the previous element to justify the new text on the svg
    """
    if isinstance(new_text, int):
        new_text = f"{new_text:,}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value.
    Prints warnings if element is not found.
    """
    element = root.find(f".//*[@id='{element_id}']")
    if element is None:
        print(f"[WARN] Element with id='{element_id}' not found.")
    else:
        element.text = new_text
        print(f"[OK] Updated id='{element_id}' with text='{new_text}'")

# ===== LOC block helpers (new) =====

def build_loc_flat_text(total, add, delete):
    """Return the flat string used to compute width and as the unstyled fallback."""
    return f"{fmt(total)} ( {fmt(add)}++, {fmt(delete)}-- )"

def fmt(n):
    return f"{int(n):,}" if isinstance(n, (int, float)) else str(n)

def update_loc_block(root, total, add, delete):
    """
    Rebuilds <tspan id="loc_block"> to contain colored child tspans for add/del,
    while treating the entire text as one logical block for width calculations.
    """
    el = root.find(".//*[@id='loc_block']")
    if el is None:
        print("[WARN] Element with id='loc_block' not found.")
        return

    # Determine the SVG namespace for creating <tspan> elements correctly
    ns = el.tag.split('}')[0].lstrip('{') if '}' in el.tag else "http://www.w3.org/2000/svg"
    tspan_tag = f"{{{ns}}}tspan"

    # Remove existing children but keep attributes
    for child in list(el):
        el.remove(child)

    # Build: "<total> ( " + <addColor>add</addColor> + "++, " + <delColor>del</delColor> + "-- )"
    total_s = fmt(total)
    add_s   = fmt(add)
    del_s   = fmt(delete)

    el.text = f"{total_s} ( "

    t_add = etree.SubElement(el, tspan_tag, {"class": "addColor"})
    t_add.text = add_s
    t_add.tail = "++, "

    t_del = etree.SubElement(el, tspan_tag, {"class": "delColor"})
    t_del.text = del_s
    t_del.tail = "-- )"

    print(f"[OK] Rebuilt 'loc_block' as: {build_loc_flat_text(total, add, delete)}")


def justify_dots(root, dots_id, full_text, target_len=30):
    """
    Justify a *_dots element against the provided full_text length.
    Ensures spaces don't collapse by setting xml:space='preserve'.
    """
    dots_el = root.find(f".//*[@id='{dots_id}']")
    if dots_el is None:
        print(f"[WARN] Dots element with id='{dots_id}' not found.")
        return

    just_len = target_len - len(full_text)
    if just_len <= 0:
        dot_string = ' '
    elif just_len == 1:
        dot_string = ' '
    elif just_len == 2:
        dot_string = '. '
    else:
        dot_string = ' ' + ('.' * just_len) + ' '

    dots_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    dots_el.text = dot_string
    print(f"[OK] Updated '{dots_id}' for width {target_len} vs text len {len(full_text)}")


def user_getter(username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']

def follower_getter(username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    Returns the function result and the time differential
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Prints a formatted time differential
    Returns formatted result if whitespace is specified, otherwise returns raw result
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


if __name__ == '__main__':
    """
    Theodore Zhu (FowlFarmer), 2022-2025
    """
    print('Calculation times:')
    # define global variable for owner ID and calculate user's creation date
    # e.g {'id': 'MDQ6VXNlcjU3MzMxMTM0'} and 2019-11-03T21:15:07Z for username 'FowlFarmer'
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)
    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2005, 9, 19))
    formatter('age calculation', age_time)
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    formatter('LOC calculation', loc_time)
    commit_data = total_loc[3]  # Get commit count from LOC query result
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    for index in range(len(total_loc)-1): total_loc[index] = '{:,}'.format(total_loc[index]) # format added, deleted, and total LOC

    svg_overwrite('/Users/theodore/Desktop/Repositories/FowlFarmer/dark.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('/Users/theodore/Desktop/Repositories/FowlFarmer/dark.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])

    # move cursor to override 'Calculation times:' with 'Total function time:' and the total function time, then move cursor back
    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
        '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % (user_time + age_time + loc_time + star_time + repo_time + contrib_time)),
        ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))