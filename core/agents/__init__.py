from agents.base import DeskAgent
from agents.business import BusinessAgent
from agents.hive_mind import HiveMindAgent
from agents.inbox import InboxAgent
from agents.plant_atlas import PlantAtlasAgent
from agents.round_table import RoundTableAgent
from agents.router import Workspace

_agents: dict[Workspace, DeskAgent] = {
    Workspace.HIVE_MIND: HiveMindAgent(),
    Workspace.BUSINESS: BusinessAgent(),
    Workspace.PLANT_ATLAS: PlantAtlasAgent(),
    Workspace.ROUND_TABLE: RoundTableAgent(),
    Workspace.INBOX: InboxAgent(),
}


def get_agent(workspace: Workspace) -> DeskAgent:
    agent = _agents.get(workspace)
    if not agent:
        return _agents[Workspace.HIVE_MIND]
    return agent
