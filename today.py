import datetime
from dateutil import relativedelta
import requests
import os
from xml.dom import minidom
import time
import hashlib
import sys

# Set this flag to True to enable debug output
DEBUG = True

# Fine-grained personal access token with All Repositories access:
HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']  # e.g., 'ktauchathuranga'
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def debug(msg):
    if DEBUG:
        print("[DEBUG]", msg)


def daily_readme(birthday):
    """Returns the length of time since I was born, e.g. 'XX years, XX months, XX days'"""
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    result = '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')
    debug("daily_readme: " + result)
    return result


def format_plural(unit):
    """Returns an 's' if unit is not 1."""
    return '' if unit == 1 else 's'


def simple_request(func_name, query, variables):
    """Sends a GraphQL request and returns the response if successful."""
    debug(f"{func_name}: Sending request with variables {variables}")
    response = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if response.status_code == 200:
        debug(f"{func_name}: Received successful response.")
        return response
    raise Exception(func_name, 'has failed with', response.status_code, response.text, QUERY_COUNT)


def graph_commits(start_date, end_date):
    """Returns total commit contributions between two dates (for a specific range)."""
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
    variables = {'start_date': start_date, 'end_date': end_date, 'login': USER_NAME}
    response = simple_request(graph_commits.__name__, query, variables)
    total = int(response.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])
    debug("graph_commits: Total contributions = " + str(total))
    return total


def lifetime_commits():
    """
    Returns the lifetime total commit contributions (across all branches)
    using the contributionsCollection from the user’s creation date.
    """
    user_info, created_at = user_getter(USER_NAME)
    from_date = created_at  # account creation date as start
    to_date = datetime.datetime.utcnow().isoformat() + "Z"
    debug(f"lifetime_commits: Calculating from {from_date} to {to_date}")
    query = '''
    query($login: String!, $from: DateTime!, $to: DateTime!) {
        user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
                totalCommitContributions
            }
        }
    }'''
    variables = {'login': USER_NAME, 'from': from_date, 'to': to_date}
    response = simple_request("lifetime_commits", query, variables)
    total = int(response.json()['data']['user']['contributionsCollection']['totalCommitContributions'])
    debug("lifetime_commits: Total lifetime commits = " + str(total))
    return total


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """Returns repository count or total stars based on count_type."""
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
    debug(f"graph_repos_stars: Fetching with cursor {cursor}")
    response = simple_request(graph_repos_stars.__name__, query, variables)
    data = response.json()['data']['user']['repositories']
    if count_type == 'repos':
        count = data['totalCount']
        debug("graph_repos_stars: Repo count = " + str(count))
        return count
    elif count_type == 'stars':
        stars = stars_counter(data['edges'])
        debug("graph_repos_stars: Total stars = " + str(stars))
        return stars


def recursive_loc(owner, repo_name, data, cache_comment, cursor=None, addition_total=0, deletion_total=0, my_commits=0):
    """
    Aggregates additions, deletions, and commit counts (authored by you)
    from a repository’s default branch. (Used for LOC caching.)
    """
    debug(f"recursive_loc: Starting for {owner}/{repo_name} with cursor {cursor}")
    while True:
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
                                        committedDate
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
        debug(f"recursive_loc: Querying commits with variables {variables}")
        response = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
        if response.status_code == 200:
            response_data = response.json()['data']['repository']
            if response_data and response_data['defaultBranchRef'] is not None:
                history = response_data['defaultBranchRef']['target']['history']
                debug(f"recursive_loc: Fetched {len(history['edges'])} commits")
                for edge in history['edges']:
                    node = edge['node']
                    if node.get('author') and node['author'].get('user') and node['author']['user']['id'] == OWNER_ID:
                        my_commits += 1
                        addition_total += node['additions']
                        deletion_total += node['deletions']
                if not history['pageInfo']['hasNextPage']:
                    debug("recursive_loc: No more pages, finishing.")
                    break
                else:
                    cursor = history['pageInfo']['endCursor']
                    debug(f"recursive_loc: Moving to next page with cursor {cursor}")
            else:
                debug(f"recursive_loc: Repository {owner}/{repo_name} is empty or missing default branch.")
                return (0, 0, 0)
        else:
            force_close_file(data, cache_comment)
            if response.status_code == 403:
                raise Exception("Too many requests in a short amount of time! You've hit the non-documented anti-abuse limit!")
            raise Exception(f'recursive_loc() failed with status: {response.status_code}, response: {response.text}')
    debug(f"recursive_loc: Completed for {owner}/{repo_name} -> commits: {my_commits}, additions: {addition_total}, deletions: {deletion_total}")
    return addition_total, deletion_total, my_commits


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    """
    Retrieves repository data (with pagination) to build/update the LOC cache.
    """
    query_count('loc_query')
    debug(f"loc_query: Fetching repositories with cursor {cursor}")
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
    response = simple_request(loc_query.__name__, query, variables)
    repos_data = response.json()['data']['user']['repositories']
    debug(f"loc_query: Retrieved {len(repos_data['edges'])} repositories")
    if repos_data['pageInfo']['hasNextPage']:
        edges += repos_data['edges']
        return loc_query(owner_affiliation, comment_size, force_cache, repos_data['pageInfo']['endCursor'], edges)
    else:
        return cache_builder(edges + repos_data['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks if repository data has changed; rebuilds the LOC cache if needed.
    """
    debug("cache_builder: Building cache...")
    cached = True
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
        debug("cache_builder: Cache file found.")
    except FileNotFoundError:
        debug("cache_builder: Cache file not found. Creating new cache.")
        data = []
        if comment_size > 0:
            for _ in range(comment_size):
                data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        debug("cache_builder: Cache rebuild needed.")
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                expected_commits = edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']
                if int(commit_count) != expected_commits:
                    debug(f"cache_builder: Repository {edges[index]['node']['nameWithOwner']} has updated commits. Recalculating LOC.")
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = repo_hash + ' ' + str(expected_commits) + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n'
            except TypeError:
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc_values = line.split()
        loc_add += int(loc_values[3])
        loc_del += int(loc_values[4])
    debug("cache_builder: Cache build complete.")
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """
    Clears the cache file and rebuilds it using the latest repository data.
    """
    debug("flush_cache: Flushing and rebuilding cache...")
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')
    debug("flush_cache: Cache flush complete.")


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """Updates specific <tspan> elements in the SVG with the provided data."""
    debug(f"svg_overwrite: Overwriting SVG file {filename}")
    svg = minidom.parse(filename)
    with open(filename, mode='w', encoding='utf-8') as f:
        tspan = svg.getElementsByTagName('tspan')
        tspan[30].firstChild.data = age_data
        tspan[65].firstChild.data = repo_data
        tspan[67].firstChild.data = contrib_data
        tspan[69].firstChild.data = commit_data
        tspan[71].firstChild.data = star_data
        tspan[73].firstChild.data = follower_data
        tspan[75].firstChild.data = loc_data[2]
        tspan[76].firstChild.data = loc_data[0] + '++'
        tspan[77].firstChild.data = loc_data[1] + '--'
        f.write(svg.toxml('utf-8').decode('utf-8'))
    debug(f"svg_overwrite: Finished updating {filename}")


def svg_element_getter(filename):
    """Prints the index and text content of each <tspan> in the SVG."""
    svg = minidom.parse(filename)
    tspan = svg.getElementsByTagName('tspan')
    for index in range(len(tspan)):
        debug(f"svg_element_getter: Element {index} - {tspan[index].firstChild.data}")


def user_getter(username):
    """
    Returns a dictionary with the user's ID and their account creation date.
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
    debug("user_getter: Fetching user data for " + username)
    response = simple_request(user_getter.__name__, query, variables)
    user_data = response.json()['data']['user']
    debug("user_getter: Received user ID " + user_data['id'])
    return {'id': user_data['id']}, user_data['createdAt']


def follower_getter(username):
    """Returns the total number of followers of the user."""
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    debug("follower_getter: Fetching followers for " + username)
    response = simple_request(follower_getter.__name__, query, {'login': username})
    count = int(response.json()['data']['user']['followers']['totalCount'])
    debug("follower_getter: " + str(count) + " followers found.")
    return count


def stars_counter(data):
    """Counts the total stars from the provided repository nodes."""
    total_stars = 0
    for node in data:
        total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def query_count(funct_id):
    """Increments the count for each GitHub API call."""
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Measures execution time of a function.
    Returns a tuple of (function_result, time_elapsed).
    """
    start = time.perf_counter()
    result = funct(*args)
    elapsed = time.perf_counter() - start
    debug(f"perf_counter: {funct.__name__} took {elapsed:.4f} seconds.")
    return result, elapsed


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Prints formatted execution time.
    Optionally returns the formatted function result.
    """
    print('{:<23}'.format('   ' + query_type + ':'), end='')
    if difference > 1:
        print('{:>12}'.format('%.4f' % difference + ' s '))
    else:
        print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


if __name__ == '__main__':
    # Check for the cache rebuild flag
    force_cache_flag = False
    if len(sys.argv) > 1 and sys.argv[1] == "--rebuild-cache":
        force_cache_flag = True
        print("Rebuilding cache as requested...")

    print('Calculation times:')
    (user_info, created_at), user_time = perf_counter(user_getter, USER_NAME)
    # Extract OWNER_ID for commit comparisons
    OWNER_ID = user_info['id']
    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2002, 7, 5))
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7, force_cache_flag)
    lifetime_commit_data, lifetime_commit_time = perf_counter(lifetime_commits)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    lifetime_commit_data = formatter('lifetime commits', lifetime_commit_time, lifetime_commit_data, 7)
    star_data = formatter('star counter', star_time, star_data)
    repo_data = formatter('my repositories', repo_time, repo_data, 2)
    contrib_data = formatter('contributed repos', contrib_time, contrib_data, 2)
    follower_data = formatter('follower counter', follower_time, follower_data, 4)

    # Format LOC numbers for display
    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite('dark_mode.svg', age_data, lifetime_commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('light_mode.svg', age_data, lifetime_commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])

    total_func_time = user_time + age_time + loc_time + lifetime_commit_time + star_time + repo_time + contrib_time
    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
          '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % total_func_time),
          ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
