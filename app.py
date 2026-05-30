@classmethod
async def fetch_playing_xi(cls, match_id: str) -> PlayingXIResponse:
    try:
        url = (
            "https://www.cricbuzz.com/cricket-match-squads/"
            f"{match_id}?_={time.time_ns()}"
        )

        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True
        ) as client:
            response = await client.get(url, headers=cls.HEADERS)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        # Title
        title = cls.clean(
            soup.title.get_text(strip=True) if soup.title else NOT_FOUND
        )

        teams: List[TeamXI] = []

        # "playing XI" h1 tag dhoondho
        xi_headers = soup.find_all(
            "h1",
            string=re.compile(r"playing\s*xi", re.I)
        )

        for header in xi_headers[:2]:
            # Header ke baad wala div container lena hai
            container = header.find_next_sibling("div")
            if not container:
                container = header.parent.find_next_sibling("div")
            if not container:
                continue

            # Team name: container ke andar h1/h2 ya pehla text block
            team_name_tag = container.find(["h1", "h2", "h3"])
            team_name = (
                cls.clean(team_name_tag.get_text(strip=True))
                if team_name_tag else NOT_FOUND
            )

            players: List[Player] = []

            # Player links: /profiles/ wale anchors
            player_links = container.find_all(
                "a",
                href=re.compile(r"/profiles/\d+/", re.I)
            )

            for a_tag in player_links:
                # Player name & role: flex-col div ke andar
                inner = a_tag.find("div", class_=re.compile(r"flex.*col|col.*flex", re.I))
                if not inner:
                    inner = a_tag

                divs = inner.find_all("div", recursive=False) if inner else []

                # Pehla div = name, doosra = role
                raw_name = divs[0].get_text(strip=True) if len(divs) > 0 else a_tag.get_text(strip=True)
                raw_role = divs[1].get_text(strip=True) if len(divs) > 1 else NOT_FOUND

                is_captain = bool(re.search(r"\(c\)|\(C\)|C\s*&\s*WK", raw_name))
                is_keeper = bool(re.search(r"\(wk\)|\(WK\)|WK|wicket", raw_name + raw_role, re.I))

                clean_name = cls.clean(
                    re.sub(r"\(c\)|\(wk\)|\(c\s*&\s*wk\)", "", raw_name, flags=re.I).strip()
                )

                if clean_name and len(clean_name) > 2:
                    players.append(Player(
                        name=clean_name,
                        role=cls.clean(raw_role),
                        is_captain=is_captain,
                        is_keeper=is_keeper,
                    ))

            if players:
                teams.append(TeamXI(team_name=team_name, players=players))

        return PlayingXIResponse(
            status="success",
            match_title=title,
            teams=teams
        )

    except httpx.TimeoutException:
        raise APIError(408, REQUEST_TIMEOUT)
    except httpx.HTTPStatusError:
        raise APIError(404, "squads data unavailable")
    except Exception:
        raise APIError(500, "failed to process squads data")
        
