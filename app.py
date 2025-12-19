from typing import List

class ThroneInheritance:

    def __init__(self, kingName: str):
        self.king_name = kingName
        self.inheritance_order = []
        self.deaths = set()
        self.children = {}

    def birth(self, parentName: str, childName: str) -> None:
        if parentName not in self.children:
            self.children[parentName] = []
        self.children[parentName].append(childName)

    def death(self, name: str) -> None:
        self.deaths.add(name)

    def getInheritanceOrder(self) -> List[str]:
        def dfs(name):
            if name not in self.deaths:
                self.inheritance_order.append(name)
            if name in self.children:
                for child in self.children[name]:
                    dfs(child)
        dfs(self.king_name)
        return self.inheritance_order

t= ThroneInheritance("king")
t.birth("king", "andy")
t.birth("king", "bob")
t.birth("king", "catherine")
t.birth("andy", "matthew")
t.birth("bob", "alex")
t.birth("bob", "asha")
t.death("bob")
print(t.getInheritanceOrder())