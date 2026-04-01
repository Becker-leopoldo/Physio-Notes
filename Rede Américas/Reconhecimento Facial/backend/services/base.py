from abc import ABC, abstractmethod
from typing import Any


class BaseService(ABC):
    """
    Contrato padrão para todos os serviços de consulta.
    Para adicionar uma nova API, crie um arquivo em services/
    herdando esta classe e implemente o método `consultar`.
    """

    @abstractmethod
    async def consultar(self, params: dict) -> Any:
        """Executa a consulta e retorna o resultado bruto da API."""
        ...
