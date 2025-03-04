import datetime
from dateutil import relativedelta
import requests
import os
from xml.dom import minidom
import time
import hashlib

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
# Issues and pull requests permissions not needed at the moment, but may be used in the future
HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']  # e.g., 'ktauchathuranga'
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def daily_readme(birthday):
    """
    Returns the length of time since I was born,
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
    Returns a properly formatted unit string.
    E.g., 1 day, 2 days
    """
    return '' if unit == 1 else 's'


def simple_request(func_name, query, variables):
    """
    Sends a GraphQL request to GitHub and returns the response.
    Raises an Exception if the response is not successful.
    """
    response = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if response.status_code == 200:
        return response
    raise Exception(func_name, 'has failed with a', response.status_code, response.text, QUERY_COUNT)


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
    variables = {'start_date': start_date, 'end_date': end_date, 'login': USER_NAME}
    response = simple_request(graph_commits.__name__, query, variables)
    return int(response.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return repository counts or total stars.
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
    response = simple_request(graph_repos_stars.__name__, query, variables)
    if count_type == 'repos':
        return response.json()['data']['user']['repositories']['totalCount']
    elif count_type == 'stars':
        return stars_counter(response.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment, cursor=None, addition_total=0, deletion_total=0, my_commits=0):
    """
    Uses GitHub's GraphQL API with cursor pagination to fetch commit stats from a repository.
    It accumulates additions, deletions, and commit counts for commits authored by me.
    """
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
        response = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
        if response.status_code == 200:
            response_data = response.json()['data']['repository']
            if response_data and response_data['defaultBranchRef'] is not None:
                history = response_data['defaultBranchRef']['target']['history']
                for edge in history['edges']:
                    node = edge['node']
                    if node.get('author') and node['author'].get('user') and node['author']['user']['id'] == OWNER_ID:
                        my_commits += 1
                        addition_total += node['additions']
                        deletion_total += node['deletions']
                if not history['pageInfo']['hasNextPage']:
                    break
                else:
                    cursor = history['pageInfo']['endCursor']
            else:
                # Repository is empty or has no default branch; return zeros.
                return (0, 0, 0)
        else:
            force_close_file(data, cache_comment)
            if response.status_code == 403:
                raise Exception("Too many requests in a short amount of time!\nYou've hit the non-documented anti-abuse limit!")
            raise Exception(f'recursive_loc() failed with status: {response.status_code}, response: {response.text}')
    return addition_total, deletion_total, my_commits


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    """
    Queries repositories (with pagination) to later calculate total lines of code via the cache.
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
    response = simple_request(loc_query.__name__, query, variables)
    repos_data = response.json()['data']['user']['repositories']
    if repos_data['pageInfo']['hasNextPage']:
        edges += repos_data['edges']
        return loc_query(owner_affiliation, comment_size, force_cache, repos_data['pageInfo']['endCursor'], edges)
    else:
        return cache_builder(edges + repos_data['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks if repositories have been updated since the last cache and updates the LOC count if needed.
    """
    cached = True  # Assume all repositories are cached
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:  # Create the cache file if it doesn't exist
        data = []
        if comment_size > 0:
            for _ in range(comment_size):
                data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]  # Save the comment block
    data = data[comment_size:]  # Remove those lines
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                expected_commits = edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']
                if int(commit_count) != expected_commits:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = repo_hash + ' ' + str(expected_commits) + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n'
            except TypeError:  # If the repo is empty
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc_values = line.split()
        loc_add += int(loc_values[3])
        loc_del += int(loc_values[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """
    Wipes the cache file and reinitializes it with new repository data.
    """
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size]  # Only save the comment block
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def commit_counter(comment_size):
    """
    Counts the total commits from the cache file.
    """
    total_commits = 0
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    with open(filename, 'r') as f:
        data = f.readlines()
    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """
    Parses an SVG file and updates specific tspan elements with the latest data.
    """
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


def svg_element_getter(filename):
    """
    Prints the index and data of every <tspan> element in the SVG.
    """
    svg = minidom.parse(filename)
    tspan = svg.getElementsByTagName('tspan')
    for index in range(len(tspan)):
        print(index, tspan[index].firstChild.data)


def user_getter(username):
    """
    Returns the account ID and creation time of the user.
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
    response = simple_request(user_getter.__name__, query, variables)
    return {'id': response.json()['data']['user']['id']}, response.json()['data']['user']['createdAt']


def follower_getter(username):
    """
    Returns the number of followers of the user.
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
    response = simple_request(follower_getter.__name__, query, {'login': username})
    return int(response.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    """
    Increments the count for each GitHub API call.
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Measures and returns the execution time of a function along with its result.
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Formats and prints the execution time.
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
    print('Calculation times:')
    # Get user account data and creation time
    (user_info, created_at), user_time = perf_counter(user_getter, USER_NAME)
    # Correctly extract OWNER_ID from the returned dict
    OWNER_ID = user_info['id']
    
    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2002, 7, 5))
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    commit_data, commit_time = perf_counter(commit_counter, 7)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    commit_data = formatter('commit counter', commit_time, commit_data, 7)
    star_data = formatter('star counter', star_time, star_data)
    repo_data = formatter('my repositories', repo_time, repo_data, 2)
    contrib_data = formatter('contributed repos', contrib_time, contrib_data, 2)
    follower_data = formatter('follower counter', follower_time, follower_data, 4)

    # Format LOC numbers
    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite('dark_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])

    # Print total function time and GitHub API call counts
    total_func_time = user_time + age_time + loc_time + commit_time + star_time + repo_time + contrib_time
    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
          '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % total_func_time),
          ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
