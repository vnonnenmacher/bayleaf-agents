from ..llm.base import LLMProvider
from ..services.phi_filter import PHIFilterClient
from ..tools.bayleaf import BayleafClient
from ..tools.documents import DocumentsToolset
from .reasoning import ReasoningBaseAgent


class LabcopilotAgent(ReasoningBaseAgent):
    def __init__(
        self,
        provider: LLMProvider,
        bayleaf: BayleafClient,
        documents_tools: DocumentsToolset | None = None,
        phi_filter: PHIFilterClient | None = None,
        decider_provider: LLMProvider | None = None,
    ):
        super().__init__(
            name="Labcopilot Agent",
            objective={
                "en-US": (
                    "You are a lab copilot agent focused on routing and grounded responses. "
                    "Decide when to call operational tools versus document retrieval. "
                    "Prefer not retrieving documents unless the request requires factual grounding."
                ),
                "pt-BR": (
                    "Você é um agente copilot de laboratório com foco em roteamento e respostas fundamentadas. "
                    "Decida quando usar ferramentas operacionais versus busca de documentos. "
                    "Prefira não consultar documentos quando não for necessário para fundamentar a resposta."
                ),
            },
            provider=provider,
            bayleaf=bayleaf,
            documents_tools=documents_tools,
            phi_filter=phi_filter,
            use_phi_filter=False,
        )
        self.decider_provider = decider_provider or provider
