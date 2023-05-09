"""Scrape GitHub data for organizational accounts."""

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import aiohttp
import networkx as nx
import os
import time

# TODO: Instead of DiGraph, use MultiDiGraph everywhere?

import sqlite3



data_root = "/Users/antonsquared/Google_Drive/PLSC_355/github-scraper/data"


finished_repo_set = set()

# mechanism for avoiding repeated work
# past_dir = "/Users/antonsquared/Google_Drive/PLSC_355/github-scraper/data/2023-04-24_04-11-31"
# finished_file_list = os.listdir(past_dir)
# for file in finished_file_list:
#     file_tokens = file.split("_")
#     org = file_tokens[0]
#     repo = file_tokens[1]
#     finished_repo_set.add((org, repo))
# print(finished_repo_set)
# print(len(finished_repo_set))


class GithubScraper:
    """Scrape information about organizational Github accounts.

    Use Github API key and user name to make requests to Github API.
    Create spreadsheets named after data type and date.


    New Data Schema:

    Attributes:
        orgs (List[str]): List of organizational Github accounts to scrape
        session (aiohttp.ClientSession): Session using Github user name and API token
    """
    def get_repo_data(repo_entry):
        if "owner" in repo_entry:
            return repo_entry['owner']['login'], repo_entry['name']
        else:
            return repo_entry['organization'], repo_entry['name']

    def __init__(
        self,
        session_list: List[aiohttp.ClientSession],
        entities: List[str] = None,
        organizations: List[str] = [],
        repos: List[str] = None,
        members: List[str] = None,
    ) -> None:
        """Instantiate object."""
        # TODO: implement a check to ensure sessions are still valid
        self.session_list = session_list
        self.num_valid_session = len(session_list)
        self.orgs = organizations
        self.counter = 0
        self.entities = entities
        self.repos = repos  # this is asymmetrical!
        self.conn = sqlite3.connect(os.path.join(
            data_root, "github_scraper_db.sqlite3"))
        # # map entities to orgs
        # self.entities = {entity:None for entity in entities}
        # # map orgs to repos
        # self.orgs = {org:None for org in organizations}
        # # map repos to commits
        # self.repos = {repo: None for repo in repos}
        # Members and repositories of listed organizations. Instantiated as empty dict
        # and only loaded if user selects operation that needs this list.
        # Saves API calls.
        self.members: Dict[str, List[str]] = {}
        # Directory to store scraped data with timestamp
        self.data_directory: Path = Path(
            Path.cwd(), "data", time.strftime("%Y-%m-%d_%H-%M-%S")
        )
        Path(self.data_directory).mkdir()

    async def wait_until_next_hour(self):
        now = time.localtime().ts_min
        await asyncio.wait((60 - now) * 60)

    def get_session(self) -> aiohttp.ClientSession:
        self.counter += 1
        return self.session_list[self.counter % self.num_valid_session]

    async def scrape_members(self) -> Dict[str, List[str]]:
        """Get list of members of specified orgs.

        Returns:
            Dict[str, List[str]]: Keys are orgs, values list of members
        """
        print("Collecting members of specified organizations...")
        members: Dict[str, List[str]] = {}
        tasks: List[asyncio.Task[Any]] = []
        for org in self.orgs:
            url = f"https://api.github.com/orgs/{org}/members"
            tasks.append(asyncio.create_task(self.call_api(url, organization=org)))
        json_org_members: List[Dict[str, Any]] = await self.load_json(tasks)
        # Extract names of org members from JSON data
        for org in self.orgs:
            members[org] = []
        for member in json_org_members:
            members[member["organization"]].append(member["login"])
        return members

    async def load_json(self, tasks: List[asyncio.Task[Any]]) -> List[Dict[str, Any]]:
        """Execute tasks with asyncio.wait() to make API calls.

        TODO: Catch when rate limit exceeded. Error message:

        {'documentation_url':
        'https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting',
        'message': 'API rate limit exceeded for user ID 8274140.'}

        TODO: Double check if you can get rid of try..except aiohttp.ContentTypeError
              and only call it in call_api instead

        Args:
            tasks (List[asyncio.Task[Any]]): List of awaitable tasks to execute

        Returns:
            List[Dict[str, Any]]: Full JSON returned by API
        """
        full_json: List[Dict[str, Any]] = []
        if not tasks:
            return full_json
        done, pending = await asyncio.wait(tasks, return_when="ALL_COMPLETED")
        for task in done:
            try:
                full_json.extend(await task)
            except aiohttp.ContentTypeError:
                # If repository is empty, pass
                pass
        return full_json

    async def call_api(self, url: str, field_parser=None, resp_parser=None, callback=None, **added_fields: str) -> List[Dict[str, Any]]:
        """Load json file using requests.

        Makes API calls and returns JSON results.

        Args:
            url (str): Github API URL to load as JSON
            **added_fields (str): Additional information that will be added to each item
                                  in the JSON data

        Returns:
            List[Dict[str, Any]]: Github URL loaded as JSON
        """
        page: int = 1
        print(f"requesting: {url}")
        json_data: List[Dict[str, Any]] = []
        # Requesting user info doesn't support pagination and returns dict, not list
        session = self.get_session()
        if url.split("/")[-2] == "users" or url.split("/")[-3] == "repos":
            async with session.get(f"{url}?per_page=100") as resp:
                member_json: Dict[str, Any] = await resp.json()
                # if "documentation_url" in member_json:
                #     sys.exit(member_json['message'])
                for key, value in added_fields.items():
                    member_json[key] = value
                json_data.append(member_json)
            return json_data
        # Other API calls return lists and should paginate
        while page < 100:
            print(f"requesting: {url}?per_page=100&page={str(page)}")

            async with session.get(f"{url}?per_page=100&page={str(page)}") as resp:
                json_page: List[Dict[str, Any]] = await resp.json()
                if json_page == []:
                    break
                #  if secondary-rate limited
                if isinstance(json_page, dict) and "documentation_url" in json_page and "message" in json_page:
                    if "rate limit exceeded" in json_page["message"]:
                        print(
                            f"rate limit exceeded, waiting unti l next hour on': {url}?per_page=100&page={str(page)}")
                        # TODO: this may cause deadlocks...should reason through it?
                        self.wait_until_next_hour()
                    elif "secondary rate" in json_page["message"]:
                        print(
                            f"secondary rate limit exceeded, waiting 2 minutes on': {url}?per_page=100&page={str(page)}")
                        await asyncio.sleep(60 * 2)
                    elif "empty" in json_page["message"]:
                        print(f"{url}?per_page=100&page={str(page)} is empty")
                        break
                    else:
                        print(
                            f"{url}?per_page=100&page={str(page)} returned an error: {json_page['message']}")
                    continue
                # if not isinstance(json_page, list):
                #     raise Exception(f"query: {url}?per_page=100&page={str(page)} returned {(type(json_page))}")
                if callable(resp_parser):
                    json_page = resp_parser(json_page)
                parsed_json_page = []
                for item in json_page:  # assumes that the return is a list of elements
                    for key, value in added_fields.items():
                        try:
                            item[key] = value
                        except TypeError as e:
                            print(f"{item=}, \n{key=},\n{json_page=}\n")
                    if callable(field_parser):
                        parsed_json_page.append(field_parser(item))
                    else:
                        parsed_json_page.append(item)

                json_data.extend(parsed_json_page)
                if len(parsed_json_page) < 100:
                    break
                page += 1
        if callable(callback):
            callback(added_fields, json_data)
        return json_data

    def generate_csv(
        self, file_name: str, json_list: List[Dict[str, Any]], columns_list: List
    ) -> None:
        """Write CSV file.

        Args:
            file_name (str): Name of the CSV file
            json_list (List[Dict[str, Any]]): JSON data to turn into CSV
            columns_list (List): List of columns that represent relevant fields
                                 in the JSON data
        """
        with open(Path(self.data_directory, file_name), "a+", encoding="utf-8") as file:
            csv_file = csv.DictWriter(
                file, fieldnames=columns_list, extrasaction="ignore"
            )
            csv_file.writeheader()
            for item in json_list:
                csv_file.writerow(item)
        print(f"- file saved as {Path('data', self.data_directory.name, file_name)}")

    async def find_organizations_for_entity(self, entity=None):
        """Find the organizations that a user or repository belongs to.

        Queries for organizations that contain the entity name;
        there does not yet appear to be any better means of analyzing this without 

        Args:
            entity (str, optional): Name of user or repository. Defaults to None.

        Returns:
            List[str]: list of organizations affiliated with the entity
        """
        if entity is None:
            return []
        # check if entity is in the db

        entity_id = self.conn.cursor().execute(
            f"SELECT id FROM entity WHERE name = ?", (entity,)).fetchone()
        if entity_id:
            return self.conn.cursor().execute(f"SELECT name FROM org WHERE entity_id= ?", (entity_id[0],)).fetchall()

        tasks = []
        query_url = f"https://api.github.com/search/users?&q={entity}+in%3Aname+type%3Aorg&type=User"
        print(f"Scraping organizations that contain entity name: {entity}")
        table_columns: List[str] = [
            "entity",
            "github_org_name",
            "id",
            "html_url",
        ]

        def entity_organizations_resp_parser(json_page):
            return json_page["items"]

        def entity_organizations_field_parser(response):
            response["github_org_name"] = response["login"]
            return response
        # async with self.session.get(query_url) as response:
        tasks.append(asyncio.create_task(self.call_api(
            query_url,
            resp_parser=entity_organizations_resp_parser,
            field_parser=entity_organizations_field_parser,
            entity=entity)))
        #  probably need a new pipeline for this

        json_orgs = await self.load_json(tasks)
        print(json_orgs)
        self.generate_csv(f"{entity}_organizations.csv", json_orgs, table_columns)
        return json_orgs

    async def scrape_entity_orgs(self) -> List[str]:
        for entity in self.entities:
            entity_org_list = await self.find_organizations_for_entity(entity)

            def org_name_parser(org):
                if "github_org_name" in org:
                    return org["github_org_name"]
                elif "name" in org:
                    return org["name"]
                else:
                    raise Exception(f"org name not found for {org}")
            org_names = {org_name_parser(org) for org in entity_org_list}
            self.orgs.extend(org_names)
        # TODO: implement saving this!

    async def scrape_org_repos(self) -> List[Dict[str, Any]]:
        """Create list of the organizations' repositories."""
        print("Scraping repositories from orgs")
        tasks: List[asyncio.Task[Any]] = []
        if self.orgs:
            for org in self.orgs:
                tasks.append(asyncio.create_task(self.call_api(url, organization=org)))
            return await self.load_json(tasks)
        else:
            raise ValueError("No organizations to scrape")

    async def scrape_repos(self) -> List[Dict[str, Any]]:
        """Create rich repo objects from a tuple list of repos"""
        print("Completing Repository data")
        tasks: List[asyncio.Task[Any]] = []
        if self.repos:
            for org, repo in self.repos:
                url = f"https://api.github.com/repos/{org}/{repo}"
                tasks.append(asyncio.create_task(self.call_api(
                    url, organization=org, repository=repo)))
            return await self.load_json(tasks)
        else:
            raise ValueError("No repositories to scrape")

    async def init_repos(self):
        if not self.repos:
            if not self.orgs:
                await self.scrape_entity_orgs()
                return await self.scrape_org_repos()
            return await self.scrape_org_repos()
        else:
            return await self.scrape_repos()

    async def create_org_repo_csv(self) -> None:
        """Write a CSV file with information about orgs' repositories."""
        # Create list of items that should appear as columns in the CSV
        table_columns: List[str] = [
            "organization",
            "name",
            "full_name",
            "stargazers_count",
            "language",
            "created_at",
            "updated_at",
            "homepage",
            "fork",
            "description",
        ]
        # no callapi here because it's handled in main as a dependency check
        self.generate_csv("org_repositories.csv", self.repos, table_columns)

    async def scrape_repo_commit_history(self) -> None:
        """Create list of commits to the organizations' repositories."""
        print("Scraping commit history")
        json_commits_all = []
        table_columns: List[str] = [
            "sha",
            "committer_name",
            "committer_email",
            "commited_at",
            "repository",
        ]

        def repo_commit_history_field_parser(item: Dict[str, Any]):
            try:
                item["sha"] = item["sha"]
                item["committer_name"] = item["commit"]["author"]["name"]
                item["committer_email"] = item["commit"]["author"]["email"]
                item["commited_at"] = item["commit"]["author"]["date"]
                item["organization"] = item["organization"]
                item["repository"] = item["repository"]
            except Exception:
                print(item)
            return item

        tasks: List[asyncio.Task[Any]] = []

        def save_commit_callback(metadata, json_data: List[Dict[str, Any]]) -> None:
            self.generate_csv(
                f"{metadata['organization']}_{metadata['repository']}_commit_history.csv", json_data, table_columns)

        for repo in self.repos:
            org_name, repo_name = GithubScraper.get_repo_data(repo)
            # check existing repo
            query = f"SELECT 1 FROM repo WHERE name = ?"
            if not self.conn.cursor().execute(query, (repo_name,)).fetchone():
                if "fork" in repo and repo["fork"] == False:
                    url = f"https://api.github.com/repos/{org_name}/{repo_name}/commits"
                    tasks.append(
                        asyncio.create_task(
                            self.call_api(url, field_parser=repo_commit_history_field_parser, callback=save_commit_callback,
                                          organization=org_name, repository=repo_name)
                        )
                    )
            else:
                print("skipping: ", org_name, repo_name, "already scraped")
        json_commits_all = await self.load_json(tasks)
        # every repo generats it's own commit history file. Ideal solution is to use a db
        # self.generate_csv("commit_history.csv", json_commits_all, table_columns)

    async def scrape_repo_contributors(self) -> None:
        """Create list of contributors to the organizations' repositories."""
        print("Scraping contributors")
        json_contributors_all = []
        graph = nx.DiGraph()
        table_columns: List[str] = [
            "organization",
            "repository",
            "login",
            "contributions",
            "html_url",
            "url",
        ]
        tasks: List[asyncio.Task[Any]] = []
        for repo in self.repos:
            org_name, repo_name = GithubScraper.get_repo_data(repo)
            url = f"https://api.github.com/repos/{org_name}/{repo_name}/contributors"
            tasks.append(
                asyncio.create_task(
                    self.call_api(url, organization={org_name}, repository=repo_name)
                )
            )
        json_contributors_all = await self.load_json(tasks)
        self.generate_csv("contributor_list.csv", json_contributors_all, table_columns)
        for contributor in json_contributors_all:
            graph.add_node(
                contributor["repository"], organization=contributor["organization"]
            )
            graph.add_edge(
                contributor["login"],
                contributor["repository"],
                organization=contributor["organization"],
            )
        nx.write_gexf(graph, Path(self.data_directory, "contributor_network.gexf"))
        print(
            "- file saved as "
            f"{Path('data', self.data_directory.name, 'contributor_network.gexf')}"
        )

    async def scrape_members_repos(self) -> None:
        """Create list of all the members of an organization and their repositories."""
        print("Getting repositories of all members.")
        json_members_repos: List[Dict[str, Any]] = []
        table_columns: List[str] = [
            "organization",
            "user",
            "full_name",
            "fork",
            "stargazers_count",
            "forks_count",
            "language",
            "description",
        ]
        tasks: List[asyncio.Task[Any]] = []
        for org in self.members:
            for member in self.members[org]:
                url = f"https://api.github.com/users/{member}/repos"
                tasks.append(
                    asyncio.create_task(
                        self.call_api(url, organization=org, user=member)
                    )
                )
        json_members_repos = await self.load_json(tasks)
        self.generate_csv("members_repositories.csv", json_members_repos, table_columns)

    async def scrape_members_info(self) -> None:
        """Gather information about the organizations' members."""
        print("Getting user information of all members.")
        table_columns: List[str] = [
            "organization",
            "login",
            "name",
            "url",
            "type",
            "company",
            "blog",
            "location",
        ]
        tasks: List[asyncio.Task[Any]] = []
        for org in self.orgs:
            for member in self.members[org]:
                url = f"https://api.github.com/users/{member}"
                tasks.append(asyncio.create_task(self.call_api(url, organization=org)))
        json_members_info: List[Dict[str, Any]] = await self.load_json(tasks)
        self.generate_csv("members_info.csv", json_members_info, table_columns)

    async def scrape_starred_repos(self) -> None:
        """Create list of all the repositories starred by organizations' members."""
        print("Getting repositories starred by members.")
        json_starred_repos_all: List[Dict[str, Any]] = []
        table_columns: List[str] = [
            "organization",
            "user",
            "full_name",
            "html_url",
            "language",
            "description",
        ]
        tasks: List[asyncio.Task[Any]] = []
        for org in self.members:
            for member in self.members[org]:
                url = f"https://api.github.com/users/{member}/starred"
                tasks.append(
                    asyncio.create_task(
                        self.call_api(url, organization=org, user=member)
                    )
                )
        json_starred_repos_all = await self.load_json(tasks)
        self.generate_csv(
            "starred_repositories.csv", json_starred_repos_all, table_columns
        )

    async def generate_follower_network(self) -> None:
        """Create full or narrow follower networks of organizations' members.

        Get every user following the members of organizations (followers)
        and the users they are following themselves (following). Then generate two
        directed graphs with NetworkX. Only includes members of specified organizations
        if in narrow follower network.

        TODO: Don't create a separate narrow follower network. Instead, try to add an
              attribute to the nodes to mark them as 'narrow' so you can filter them out
              in Gephi. Will simplify this function, but double check that this works
              correctly before you remove the code for generating narrow follower
              networks
        """
        print("Generating follower networks")
        # Create graph dict and add self.members as nodes
        graph_full = nx.DiGraph()
        graph_narrow = nx.DiGraph()
        for org in self.orgs:
            for member in self.members[org]:
                graph_full.add_node(member, organization=org)
                graph_narrow.add_node(member, organization=org)

        # Get followers and following for each member and build graph
        tasks_followers: List[asyncio.Task[Any]] = []
        tasks_following: List[asyncio.Task[Any]] = []
        for org in self.members:
            for member in self.members[org]:
                url_followers = f"https://api.github.com/users/{member}/followers"
                tasks_followers.append(
                    asyncio.create_task(
                        self.call_api(url_followers, follows=member, original_org=org)
                    )
                )
                url_following = f"https://api.github.com/users/{member}/following"
                tasks_following.append(
                    asyncio.create_task(
                        self.call_api(
                            url_following, followed_by=member, original_org=org
                        )
                    )
                )
        json_followers = await self.load_json(tasks_followers)
        json_following = await self.load_json(tasks_following)
        # Build full and narrow graphs
        for follower in json_followers:
            graph_full.add_edge(
                follower["login"],
                follower["follows"],
                organization=follower["original_org"],
            )
            if follower["login"] in self.members[follower["original_org"]]:
                graph_narrow.add_edge(
                    follower["login"],
                    follower["follows"],
                    organization=follower["original_org"],
                )
        for following in json_following:
            graph_full.add_edge(
                following["followed_by"],
                following["login"],
                organization=following["original_org"],
            )
            if following["login"] in self.members[following["original_org"]]:
                graph_narrow.add_edge(
                    following["followed_by"],
                    following["login"],
                    organization=following["original_org"],
                )
        # Write graphs and save files
        nx.write_gexf(
            graph_full, Path(self.data_directory, "full-follower-network.gexf")
        )
        nx.write_gexf(
            graph_narrow, Path(self.data_directory, "narrow-follower-network.gexf")
        )
        print(
            f"- files saved in {Path('data', self.data_directory.name)} as "
            "full-follower-network.gexf and narrow-follower-network.gexf"
        )

    async def generate_memberships_network(self) -> None:
        """Take all the members of the organizations and generate a directed graph.

        This shows creates a network with the organizational memberships.
        """
        print("Generating network of memberships.")
        graph = nx.DiGraph()
        tasks: List[asyncio.Task[Any]] = []
        for org in self.members:
            for member in self.members[org]:
                url = f"https://api.github.com/users/{member}/orgs"
                tasks.append(
                    asyncio.create_task(
                        self.call_api(url, organization=org, scraped_org_member=member)
                    )
                )
        json_org_memberships = await self.load_json(tasks)
        for membership in json_org_memberships:
            graph.add_node(membership["scraped_org_member"], node_type="user")
            graph.add_edge(
                membership["scraped_org_member"],
                membership["login"],  # name of organization user is member of
                node_type="organization",
            )
        nx.write_gexf(graph, Path(self.data_directory, "membership_network.gexf"))
        print(
            "- file saved as "
            f"{Path('data', self.data_directory.name, 'membership_network.gexf')}"
        )


def read_config() -> List[Tuple[str, str]]:
    """Read config file.

    Returns:
        Tuple[str, str]: Github user name and API token

    Raises:
        KeyError: If config file is empty
    """
    try:
        with open(Path(Path.cwd(), "config.json"), "r", encoding="utf-8") as file:
            auth_list = []
            config = json.load(file)
            if isinstance(config, list):
                for elem in config:
                    user: str = elem["user_name"]
                    api_token: str = elem["api_token"]
                    if user == "" or api_token == "":
                        raise KeyError
                    else:
                        auth_list.append((user, api_token))
            return auth_list
    except (FileNotFoundError, KeyError):
        sys.exit(
            "Failed to read Github user name and/or API token. "
            "Please add them to the config.json file."
        )


def read_entities(filename: str = None) -> List[str]:
    """Read list of organizations from file.

    Returns:
        List[str]: List of names of organizational Github accounts
    """
    orgs: List[str] = []
    if not filename:
        filename = "entities.csv"
    with open(Path(Path.cwd(), filename), "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            orgs.append(row["entity_name"])
    if not orgs:
        sys.exit(
            "No organizations to scrape found in organizations.csv. "
            "Please add the names of the organizations you want to scrape "
            "in the column 'github_org_name' (one name per row)."
        )
    return orgs


def read_organizations(filename: str = None) -> List[str]:
    """Read list of organizations from file.

    Returns:
        List[str]: List of names of organizational Github accounts
    """
    orgs: List[str] = []
    if not filename:
        filename = "organizations.csv"
    with open(Path(Path.cwd(), filename), "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            orgs.append(row["github_org_name"])
    if not orgs:
        sys.exit(
            "No organizations to scrape found in organizations.csv. "
            "Please add the names of the organizations you want to scrape "
            "in the column 'github_org_name' (one name per row)."
        )
    return orgs

# #  TODO: generalize the caching layer to work with any API call


def read_repos(filename: str = None) -> List[str]:
    repos: List[Dict[str, any]] = []
    if not filename:
        filename = "repos.csv"
    with open(Path(Path.cwd(), filename), "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        table_columns: List[str] = [
            "organization",
            "name",
            "full_name",
            "stargazers_count",
            "language",
            "created_at",
            "updated_at",
            "homepage",
            "fork",
            "description",
        ]
        for row in reader:
            row_dict = {}
            for col in table_columns:
                if col in row:
                    row_dict[col] = row[col]
            repos.append(row_dict)
    if not repos:
        sys.exit(
            "No organizations to scrape found in organizations.csv. "
            "Please add the names of the organizations you want to scrape "
            "in the column 'github_org_name' (one name per row)."
        )
    print(repos)
    print("REPOS FINISHED")
    return repos


def parse_args() -> Dict[str, bool]:
    """Parse arguments.

    We use the 'dest' value to map args with functions/methods. This way, we
    can use getattr(object, dest)() and avoid long if...then list in main().

    Returns:
        Dict[str, bool]: Result of vars(parse_args())
    """
    argparser = argparse.ArgumentParser(
        description="Scrape organizational accounts on Github."
    )
    argparser.add_argument(
        "--load-entities",
        "-le",
        help=" CSV File containing list of entities to scrape."
    )
    argparser.add_argument(
        "--load-organizations",
        "-lo",
        help="CSV File containing list of organizations to scrape."
    )
    argparser.add_argument(
        "--load-repositories",
        "-lr",
        help=" CSV File containing list of repositories to scrape."
    )
    argparser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="scrape all the information listed below",
    )
    argparser.add_argument(
        "--repos",
        "-r",
        action="store_true",
        dest="create_org_repo_csv",
        help="scrape the organizations' repositories (CSV)",
    )
    argparser.add_argument(
        "--commit-history",
        "-ch",
        action="store_true",
        dest="scrape_repo_commit_history",
        help="scrape the commit history of all of the organizations' repositories (CSV)",
    )
    argparser.add_argument(
        "--contributors",
        "-c",
        action="store_true",
        dest="scrape_repo_contributors",
        help="scrape contributors of the organizations' repositories (CSV and GEXF)",
    )
    argparser.add_argument(
        "--member_repos",
        "-mr",
        action="store_true",
        dest="scrape_members_repos",
        help="scrape all repositories owned by the members of the organizations (CSV)",
    )
    argparser.add_argument(
        "--member_infos",
        "-mi",
        action="store_true",
        dest="scrape_members_info",
        help="scrape information about each member of the organizations (CSV)",
    )
    argparser.add_argument(
        "--starred",
        "-s",
        action="store_true",
        dest="scrape_starred_repos",
        help="scrape all repositories starred by members of the organizations (CSV)",
    )
    argparser.add_argument(
        "--followers",
        "-f",
        action="store_true",
        dest="generate_follower_network",
        help="generate a follower network. Creates full and narrow network graph, the "
        "latter only shows how scraped organizations are networked among each "
        "other (two GEXF files)",
    )
    argparser.add_argument(
        "--memberships",
        "-m",
        action="store_true",
        dest="generate_memberships_network",
        help="scrape all organizational memberships of org members (GEXF)",
    )
    argparser.add_argument(
        "--entity_organizations",
        "-eo",
        dest="find_organizations_for_entity",
        help="find all organizations affiliated with a certain entity"
    )
    args: Dict[str, bool] = vars(argparser.parse_args())
    return args


async def main() -> None:
    """Set up GithubScraper object."""
    args: Dict[str, bool] = parse_args()
    if not any(args.values()):
        sys.exit(
            "You need to provide at least one argument. "
            "For usage, call: github_scraper -h"
        )
    auth_list = read_config()

    # To avoid unnecessary API calls, only get org members and repos if needed
    require_members = [
        "scrape_members_repos",
        "scrape_members_info",
        "scrape_starred_repos",
        "generate_follower_network",
        "generate_memberships_network",
    ]
    require_repos = ["create_org_repo_csv",
                     "scrape_repo_contributors", "scrape_repo_commit_history"]
    # Start aiohttp session
    session_list = []
    for creds in auth_list:
        # safety check on username, token done earlier
        auth = aiohttp.BasicAuth(creds[0], creds[1])
        session_list.append(aiohttp.ClientSession(auth=auth))
    print(args)
    if args["load_entities"]:
        entities = read_entities(args["load_entities"])
        github_scraper = GithubScraper(session_list, entities=entities)
    elif args["load_organizations"]:
        organizations = read_organizations(args["load_organizations"])
        github_scraper = GithubScraper(session_list, organizations=organizations)
    elif args["load_repositories"]:
        repos = read_repos(args["load_repositories"])
        print(repos)
        github_scraper = GithubScraper(session_list, repos=repos)
    else:
        github_scraper = GithubScraper(session_list)
    # If --all was provided, simply run everything
    if args["all"]:
        github_scraper.members = await github_scraper.scrape_members()
        github_scraper.repos = await github_scraper.init_repos()
        for arg in args:
            if arg != "all" and arg != "find_organizations_for_entity":
                await getattr(github_scraper, arg)()
    else:
        # Check args provided, get members/repos if necessary, call related methods
        called_args = [arg for arg, value in args.items() if value]

        if any(arg for arg in called_args if arg in require_members):
            github_scraper.members = await github_scraper.members()
        if any(arg for arg in called_args if arg in require_repos) and not github_scraper.repos:
            github_scraper.repos = await github_scraper.init_repos()
        for arg in called_args:
            # boolean, meaning no argument
            if args[arg] is True and hasattr(github_scraper, arg):
                await getattr(github_scraper, arg)()
            # truthy, meaning there's an argument
            elif args[arg] and hasattr(github_scraper, arg):
                await getattr(github_scraper, arg)(args[arg])


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
