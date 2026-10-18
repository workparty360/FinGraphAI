"""Regression coverage: annual financial facts must not rely on nearest-neighbor ranking."""
from falkor_memory import FalkorMemory

class Vector:
    def embed_query(self, text):
        return [0.1, 0.2, 0.3]

class Rows:
    def __init__(self, result_set):
        self.result_set = result_set

class Graph:
    def __init__(self):
        self.reads = []
        self.writes = []
    def create_node_vector_index(self, *args, **kwargs):
        return None
    def query(self, cypher, params=None):
        self.writes.append((cypher, params))
        return Rows([['ok']])
    def ro_query(self, cypher, params=None):
        self.reads.append((cypher, params))
        if 'd.period=$period' in cypher:
            return Rows([['Financial annual income statement; period: 2023. {"Net profit": 96995000000}', 'sec_annual_income', 'AAPL', '2023']])
        return Rows([])

def test_year_specific_income_is_retrieved_and_tenant_scoped():
    graph = Graph()
    memory = FalkorMemory('tenant-1', 'user-1', Vector(), graph=graph, vector_dimension=3)
    memory.upsert_document('Financial annual income statement; period: 2023', ticker='AAPL', source='sec_annual_income', period='2023')
    assert graph.writes[-1][1]['period'] == '2023'
    results = memory.exact_financial_documents(ticker='aapl', year=2023)
    assert '96995000000' in results[0]['content']
    cypher, params = graph.reads[-1]
    assert params['ticker'] == 'AAPL' and params['period'] == '2023'
    assert params['user_id'] == 'user-1' and params['tenant_id'] == 'tenant-1'
    assert 'sec_annual_income' in cypher and 'sec_annual_balance' in cypher
