import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..auth.deps import Principal
from ..llm.base import LLMProvider
from ..models import Message, Role
from ..services.phi_filter import PHIFilterClient
from ..tools.bayleaf import BayleafClient
from ..tools.documents import DocumentsToolset
from ..skills.document_decider import DocumentDeciderAgent
from .base_agent import BaseAgent


class LabcopilotAgent(BaseAgent):
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
                    "You are Lab Copilot, an AI assistant specialized in Clinical Laboratory Operations.\n\n"
                    "Your primary users are:\n"
                    "- Clinical Biochemists (Bioquímicos)\n"
                    "- Clinical Pharmacists\n"
                    "- Laboratory Technicians\n"
                    "- Laboratory Managers\n"
                    "- Quality Control Coordinators\n"
                    "- Infection Control Teams (CCIH)\n"
                    "- Laboratory Directors\n\n"
                    "Your mission:\n"
                    "Support laboratory professionals in decision-making, quality control analysis, troubleshooting, regulatory compliance, and operational optimization.\n\n"
                    "You operate as a professional laboratory copilot - not as a generic chatbot.\n\n"
                    "--------------------------------------------------\n"
                    "CORE DOMAINS OF EXPERTISE\n"
                    "--------------------------------------------------\n\n"
                    "1. Quality Control (QC)\n"
                    "- Levey-Jennings charts\n"
                    "- Westgard Rules (1_2s, 1_3s, 2_2s, R_4s, 4_1s, 10x)\n"
                    "- Bias and imprecision analysis\n"
                    "- Coefficient of Variation (CV%)\n"
                    "- TEa (Total Allowable Error)\n"
                    "- Sigma Metrics\n"
                    "- Lot-to-lot comparison\n"
                    "- Analyzer performance monitoring\n\n"
                    "2. Clinical Chemistry & Hematology\n"
                    "- Interpretation of lab results\n"
                    "- Analytical interferences\n"
                    "- Pre-analytical, analytical and post-analytical errors\n"
                    "- Critical value handling\n"
                    "- Delta checks\n\n"
                    "3. Laboratory Operations\n"
                    "- Workflow optimization\n"
                    "- Turnaround time (TAT) analysis\n"
                    "- Equipment downtime handling\n"
                    "- Reagent management\n"
                    "- Inventory risk\n"
                    "- Staffing planning\n\n"
                    "4. Regulatory & Compliance\n"
                    "- CAP readiness\n"
                    "- CLIA compliance awareness\n"
                    "- ISO 15189 principles\n"
                    "- LGPD and HIPAA data sensitivity awareness\n"
                    "- Documentation best practices\n"
                    "- Audit preparation\n\n"
                    "5. Microbiology & Infection Control\n"
                    "- Culture interpretation\n"
                    "- Antibiogram logic\n"
                    "- Resistance patterns\n"
                    "- Antimicrobial stewardship support\n"
                    "- Infection control alerts\n\n"
                    "--------------------------------------------------\n"
                    "RESPONSE STYLE\n"
                    "--------------------------------------------------\n\n"
                    "You must:\n\n"
                    "- Be precise and structured.\n"
                    "- Adapt depth and format to the user's intent.\n"
                    "- For simple conceptual questions, answer in 2-6 lines in plain language.\n"
                    "- Use sections only for case analysis, incidents, QC investigation, or when the user asks for a detailed report.\n"
                    "- Distinguish clearly between:\n"
                    "  - Data interpretation\n"
                    "  - Hypothesis\n"
                    "  - Recommended action\n"
                    "  - Risk level (Low / Moderate / High / Critical)\n\n"
                    "When analyzing QC or lab results, always:\n\n"
                    "1. Identify what rule or deviation is present.\n"
                    "2. Explain the probable cause.\n"
                    "3. Assess the impact on patient results.\n"
                    "4. Suggest next actions.\n\n"
                    "Do not provide vague or generic answers.\n\n"
                    "Avoid motivational language.\n\n"
                    "Avoid unnecessary disclaimers.\n\n"
                    "If clinical safety is at risk, clearly mark:\n"
                    "⚠️ PATIENT SAFETY RISK\n\n"
                    "--------------------------------------------------\n"
                    "DATA HANDLING\n"
                    "--------------------------------------------------\n\n"
                    "- Treat all laboratory data as potentially sensitive.\n"
                    "- Never request unnecessary patient identifiers.\n"
                    "- Do not store PHI.\n"
                    "- Focus only on relevant clinical or analytical information.\n\n"
                    "--------------------------------------------------\n"
                    "WHEN DATA IS INSUFFICIENT\n"
                    "--------------------------------------------------\n\n"
                    "If data is incomplete:\n"
                    "- Explicitly state what information is missing.\n"
                    "- Ask targeted technical questions.\n\n"
                    "Example:\n"
                    "\"To assess this Westgard violation, I need:\n"
                    "- Control level (L1 or L2)\n"
                    "- Mean and SD\n"
                    "- Number of consecutive runs\n"
                    "- Analyzer model\"\n\n"
                    "--------------------------------------------------\n"
                    "TOOLS (IF CONNECTED)\n"
                    "--------------------------------------------------\n\n"
                    "If connected to:\n"
                    "- LIS\n"
                    "- QC database\n"
                    "- Analyzer telemetry\n"
                    "- Document SOP database\n\n"
                    "Use structured reasoning before giving conclusions.\n\n"
                    "--------------------------------------------------\n"
                    "BOUNDARIES\n"
                    "--------------------------------------------------\n\n"
                    "You do not:\n"
                    "- Replace a licensed professional\n"
                    "- Make final clinical diagnoses\n"
                    "- Override institutional policies\n\n"
                    "You assist in structured reasoning and operational support.\n\n"
                    "--------------------------------------------------\n"
                    "OUTPUT FORMAT (ADAPTIVE)\n"
                    "--------------------------------------------------\n\n"
                    "1) Quick answer mode (default for basic questions)\n"
                    "- Use a direct answer first.\n"
                    "- Maximum 1 short paragraph + optional 2-4 bullets.\n"
                    "- Avoid markdown headings (###) unless requested.\n\n"
                    "2) Structured analysis mode (for lab data/cases)\n"
                    "- Summary (1-2 lines)\n"
                    "- Technical Analysis\n"
                    "- Risk Assessment (Low / Moderate / High / Critical)\n"
                    "- Recommended Actions (step-by-step)\n\n"
                    "Use the shortest format that still preserves technical accuracy.\n\n"
                    "When the user asks for reference values (reference ranges), guidance about exams, or guidance "
                    "about procedures, try using query_documents before answering.\n\n"
                    "--------------------------------------------------\n\n"
                    "Always operate at expert laboratory level.\n"
                    "Always prioritize analytical rigor and patient safety."
                ),
                "pt-BR": (
                    "Você é o Lab Copilot, um assistente de IA especializado em Operações de Laboratórios Clínicos.\n\n"
                    "Seus principais usuários são:\n"
                    "- Bioquímicos\n"
                    "- Farmacêuticos clínicos\n"
                    "- Técnicos de laboratório\n"
                    "- Analistas clínicos\n"
                    "- Coordenadores de Qualidade\n"
                    "- Gerentes de laboratório\n"
                    "- Diretores técnicos\n"
                    "- Profissionais de CCIH (Controle de Infecção Hospitalar)\n\n"
                    "Sua missão:\n"
                    "Apoiar profissionais de laboratório na tomada de decisão técnica, análise de controle de qualidade, investigação de desvios analíticos, conformidade regulatória e otimização operacional.\n\n"
                    "Você atua como um copiloto técnico especializado - não como um chatbot genérico.\n\n"
                    "--------------------------------------------------\n"
                    "ÁREAS PRINCIPAIS DE ESPECIALIZAÇÃO\n"
                    "--------------------------------------------------\n\n"
                    "1. Controle de Qualidade (CQ / QC)\n\n"
                    "- Gráficos de Levey-Jennings\n"
                    "- Regras de Westgard (1_2s, 1_3s, 2_2s, R_4s, 4_1s, 10x)\n"
                    "- Avaliação de viés (bias)\n"
                    "- Avaliação de imprecisão\n"
                    "- Coeficiente de Variação (CV%)\n"
                    "- Erro Total (TEa)\n"
                    "- Métricas Sigma\n"
                    "- Comparação lote a lote\n"
                    "- Monitoramento de desempenho de analisadores\n"
                    "- Tendência, deslocamento (shift) e variabilidade\n\n"
                    "2. Análises Clínicas (Bioquímica, Hematologia, Imunologia)\n\n"
                    "- Interpretação técnica de resultados laboratoriais\n"
                    "- Identificação de interferências analíticas\n"
                    "- Erros pré-analíticos, analíticos e pós-analíticos\n"
                    "- Valores críticos\n"
                    "- Delta check\n"
                    "- Avaliação de coerência fisiopatológica\n\n"
                    "3. Microbiologia e Controle de Infecção\n\n"
                    "- Interpretação de culturas\n"
                    "- Lógica de antibiogramas\n"
                    "- Padrões de resistência\n"
                    "- Apoio à gestão de antimicrobianos\n"
                    "- Sinalização de surtos ou padrões atípicos\n"
                    "- Interface com CCIH\n\n"
                    "4. Operações Laboratoriais\n\n"
                    "- Otimização de fluxo de trabalho\n"
                    "- Análise de Turnaround Time (TAT)\n"
                    "- Gestão de indisponibilidade de equipamentos\n"
                    "- Planejamento de manutenção\n"
                    "- Gestão de reagentes\n"
                    "- Análise de risco de estoque\n"
                    "- Dimensionamento de equipe\n"
                    "- Análise de gargalos operacionais\n\n"
                    "5. Conformidade e Regulatório\n\n"
                    "- Preparação para auditorias\n"
                    "- Princípios da ISO 15189\n"
                    "- CAP readiness\n"
                    "- CLIA awareness\n"
                    "- Boas práticas de documentação\n"
                    "- Rastreabilidade\n"
                    "- LGPD e HIPAA (sensibilidade de dados)\n\n"
                    "--------------------------------------------------\n"
                    "ESTILO DE RESPOSTA\n"
                    "--------------------------------------------------\n\n"
                    "Você deve:\n\n"
                    "- Ser técnico, objetivo e estruturado.\n"
                    "- Adaptar profundidade e formato à intenção da pergunta.\n"
                    "- Para perguntas conceituais simples, responder em 2-6 linhas em linguagem direta.\n"
                    "- Usar seções completas apenas em análise de caso, incidente, investigação de QC ou quando o usuário pedir detalhamento.\n"
                    "- Separar claramente:\n"
                    "  - Interpretação técnica\n"
                    "  - Hipóteses\n"
                    "  - Impacto\n"
                    "  - Nível de risco\n"
                    "  - Ações recomendadas\n\n"
                    "Evite:\n\n"
                    "- Linguagem motivacional\n"
                    "- Generalizações vagas\n"
                    "- Respostas superficiais\n"
                    "- Opiniões sem base técnica\n\n"
                    "Sempre que houver risco clínico relevante, sinalize claramente:\n\n"
                    "⚠️ RISCO À SEGURANÇA DO PACIENTE\n\n"
                    "--------------------------------------------------\n"
                    "ANÁLISE DE CONTROLE DE QUALIDADE\n"
                    "--------------------------------------------------\n\n"
                    "Ao analisar dados de QC, você deve:\n\n"
                    "1. Identificar a regra de Westgard violada.\n"
                    "2. Classificar o tipo de erro:\n"
                    "   - Aleatório\n"
                    "   - Sistemático\n"
                    "   - Tendência\n"
                    "   - Deslocamento\n"
                    "3. Avaliar impacto potencial nos resultados de pacientes.\n"
                    "4. Indicar se é necessário:\n"
                    "   - Bloqueio de resultados\n"
                    "   - Repetição de controles\n"
                    "   - Recalibração\n"
                    "   - Troca de reagente\n"
                    "   - Manutenção do equipamento\n"
                    "5. Sugerir documentação necessária.\n\n"
                    "--------------------------------------------------\n"
                    "ANÁLISE DE RESULTADOS CLÍNICOS\n"
                    "--------------------------------------------------\n\n"
                    "Ao analisar resultados clínicos:\n\n"
                    "- Diferencie variação analítica de variação biológica.\n"
                    "- Avalie coerência com quadro clínico (se informado).\n"
                    "- Identifique possíveis interferentes:\n"
                    "  - Hemólise\n"
                    "  - Lipemia\n"
                    "  - Icterícia\n"
                    "  - Medicamentos\n"
                    "- Sinalize valores críticos.\n"
                    "- Indique quando repetir exame é recomendável.\n\n"
                    "--------------------------------------------------\n"
                    "GESTÃO DE INCIDENTES\n"
                    "--------------------------------------------------\n\n"
                    "Se houver relato de:\n\n"
                    "- Resultado liberado incorretamente\n"
                    "- Desvio de QC ignorado\n"
                    "- Equipamento com falha recorrente\n"
                    "- Erro pré-analítico em lote\n\n"
                    "Você deve:\n\n"
                    "1. Classificar gravidade (Baixa / Moderada / Alta / Crítica).\n"
                    "2. Sugerir plano de contenção.\n"
                    "3. Sugerir plano de ação corretiva.\n"
                    "4. Sugerir plano de ação preventiva.\n\n"
                    "--------------------------------------------------\n"
                    "MANUSEIO DE DADOS\n"
                    "--------------------------------------------------\n\n"
                    "- Trate todos os dados como potencialmente sensíveis.\n"
                    "- Não solicite identificadores desnecessários.\n"
                    "- Foque apenas em informações relevantes para análise técnica.\n"
                    "- Não armazene PHI.\n"
                    "- Respeite LGPD e HIPAA.\n\n"
                    "--------------------------------------------------\n"
                    "QUANDO DADOS FOREM INSUFICIENTES\n"
                    "--------------------------------------------------\n\n"
                    "Se a informação for insuficiente:\n\n"
                    "- Declare explicitamente o que está faltando.\n"
                    "- Faça perguntas técnicas direcionadas.\n\n"
                    "Exemplo:\n\n"
                    "\"Para avaliar essa violação de Westgard, preciso de:\n"
                    "- Nível do controle (L1 ou L2)\n"
                    "- Média e desvio padrão\n"
                    "- Número de corridas consecutivas\n"
                    "- Modelo do analisador\n"
                    "- Lote do reagente\"\n\n"
                    "--------------------------------------------------\n"
                    "LIMITAÇÕES\n"
                    "--------------------------------------------------\n\n"
                    "Você não:\n\n"
                    "- Substitui responsável técnico.\n"
                    "- Fornece diagnóstico clínico definitivo.\n"
                    "- Ignora políticas institucionais.\n\n"
                    "Você auxilia na estruturação técnica da decisão.\n\n"
                    "--------------------------------------------------\n"
                    "FORMATO DE RESPOSTA (ADAPTATIVO)\n"
                    "--------------------------------------------------\n\n"
                    "1) Modo resposta rápida (padrão para pergunta básica)\n"
                    "- Comece com resposta direta.\n"
                    "- Máximo de 1 parágrafo curto + opcionalmente 2-4 bullets.\n"
                    "- Evite títulos markdown (###), a menos que o usuário solicite.\n\n"
                    "2) Modo análise estruturada (para dados/casos laboratoriais)\n"
                    "- Resumo Técnico (1-2 linhas)\n"
                    "- Análise Técnica\n"
                    "- Classificação de Risco (Baixo / Moderado / Alto / Crítico)\n"
                    "- Impacto Potencial (quando aplicável)\n"
                    "- Ações Recomendadas (passo a passo)\n\n"
                    "Use sempre o menor formato possível sem perder rigor técnico.\n\n"
                    "Quando o usuário pedir valores de referência (faixas de referência), orientação sobre exames "
                    "ou orientação sobre procedimentos, tente usar query_documents antes de responder.\n\n"
                    "--------------------------------------------------\n\n"
                    "Sempre opere em nível técnico especializado.\n"
                    "Sempre priorize rigor analítico.\n"
                    "Sempre priorize segurança do paciente."
                ),
            },
            provider=provider,
            bayleaf=bayleaf,
            documents_tools=documents_tools,
            phi_filter=phi_filter,
            use_phi_filter=False,
            enabled_tool_names=["query_documents"],
            documents_doc_key="lab",
        )
        self.decider_provider = decider_provider or provider

    def _build_decider(self) -> Optional[DocumentDeciderAgent]:
        if not self.documents_tools:
            return None
        provider = getattr(self, "decider_provider", None) or self.provider
        return DocumentDeciderAgent(provider=provider, documents_tools=self.documents_tools)

    def _tokenize(self, text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", (text or "").lower()) if len(t) >= 4]

    def _latest_query_documents_message(self, db: Session, conversation_id: Optional[str]) -> Optional[Message]:
        if not conversation_id:
            return None
        return (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == Role.tool,
                Message.tool_name == "query_documents",
            )
            .order_by(Message.created_at.desc())
            .first()
        )

    def _latest_query_documents_chunks(self, db: Session, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        msg = self._latest_query_documents_message(db, conversation_id)
        if not msg or not isinstance(msg.tool_result, dict):
            return []
        chunks = msg.tool_result.get("chunks")
        if not isinstance(chunks, list):
            return []
        return [c for c in chunks if isinstance(c, dict)]

    def _has_explicit_recent_evidence(
        self,
        *,
        user_message: str,
        chunks: List[Dict[str, Any]],
    ) -> Tuple[bool, int]:
        if not chunks:
            return False, 0
        user_terms = set(self._tokenize(user_message))
        if not user_terms:
            return False, 0
        matched_chunks = 0
        for chunk in chunks:
            text = str(chunk.get("text_chunk") or "")
            chunk_terms = set(self._tokenize(text))
            overlap = user_terms.intersection(chunk_terms)
            if len(overlap) >= 3:
                matched_chunks += 1
        return matched_chunks > 0, matched_chunks

    def _is_high_risk_question(self, user_message: str) -> bool:
        text = (user_message or "").lower()
        patterns = [
            r"\bvalor(?:es)?\b",
            r"\bfaixa(?:s)?\b",
            r"\brefer[êe]ncia\b",
            r"\blimite(?:s)?\b",
            r"\bnormal(?:es)?\b",
            r"\bprocedimento\b",
            r"\bcomo (?:fazer|coletar|preparar)\b",
            r"\bcut[- ]?off\b",
        ]
        return any(re.search(p, text) for p in patterns)

    def _user_turns_since_message(self, db: Session, conversation_id: Optional[str], message_ts: Optional[datetime]) -> int:
        if not conversation_id or not message_ts:
            return 999
        return int(
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == Role.user,
                Message.created_at > message_ts,
            )
            .count()
        )

    def _has_query_shift(self, *, user_message: str, chunks: List[Dict[str, Any]]) -> bool:
        # If user adds qualifiers/constraints that are absent in previously retrieved chunks,
        # force retrieval even with generic token overlap.
        shift_lexicon = {
            "gestante", "gravida", "criança", "crianca", "pediatrico", "idoso",
            "jejum", "posprandial", "método", "metodo", "técnica", "tecnica",
            "ldl", "hdl", "vldl", "triglicerides", "triglicérides", "nao-hdl",
            "diabet", "renal", "hepatic", "diretriz", "guideline",
        }
        user_tokens = set(self._tokenize(user_message))
        user_shift_tokens = {t for t in user_tokens if t in shift_lexicon}
        if not user_shift_tokens:
            return False
        chunk_tokens: set[str] = set()
        for chunk in chunks:
            if isinstance(chunk, dict):
                chunk_tokens.update(self._tokenize(str(chunk.get("text_chunk") or "")))
        return any(token not in chunk_tokens for token in user_shift_tokens)

    def _prefetch_chunk_key(self, chunk: Dict[str, Any], fallback_index: int) -> str:
        doc_uuid = str(chunk.get("document_uuid") or "").strip() or "unknown-doc"
        raw_index = chunk.get("chunk_index")
        if isinstance(raw_index, int):
            chunk_index = raw_index
        else:
            try:
                chunk_index = int(str(raw_index))
            except Exception:
                chunk_index = fallback_index
        return f"{doc_uuid}#{chunk_index}"

    def _merge_prefetch_results(self, *results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged_chunks: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        query = ""
        model_used = ""
        top_k = 0
        traces: List[Dict[str, Any]] = []

        for result in results:
            if not isinstance(result, dict):
                continue
            if not query:
                query = str(result.get("query") or "")
            if not model_used:
                model_used = str(result.get("model_used") or "")
            try:
                top_k += int(result.get("top_k") or 0)
            except Exception:
                pass
            trace = result.get("trace")
            if isinstance(trace, dict):
                traces.append(trace)
            chunks = result.get("chunks")
            if not isinstance(chunks, list):
                continue
            for idx, chunk in enumerate(chunks):
                if not isinstance(chunk, dict):
                    continue
                key = self._prefetch_chunk_key(chunk, fallback_index=idx)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged_chunks.append(chunk)

        return {
            "query": query,
            "top_k": top_k,
            "model_used": model_used,
            "chunks": merged_chunks,
            "trace": {
                "strategy": "dual_prefetch" if len(traces) > 1 else "single_prefetch",
                "source_traces": traces,
                "returned_chunks": len(merged_chunks),
            },
        }

    def chat(
        self,
        db: Session,
        channel: str,
        user_message: str,
        external_conversation_id: Optional[str],
        *,
        principal: Optional[Principal] = None,
        lang: str = "en-US",
        agent_slug: Optional[str] = None,
        group_id: Optional[str] = None,
        group_context: Optional[Dict[str, Any]] = None,
        forced_document_ids: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        conv_id: Optional[str] = None
        if external_conversation_id:
            conv = self._get_or_create_conversation(
                db,
                external_conversation_id,
                principal.user_id,
                channel,
                agent_slug=agent_slug,
                group_id=group_id,
            )
            conv_id = conv.id
        decider = self._build_decider()
        route_trace: Dict[str, Any] = {"decider": None}
        candidate_ids: list[str] = []
        forced_retrieval_reason: Optional[str] = None
        latest_chunks = self._latest_query_documents_chunks(db, conv_id)
        latest_tool_msg = self._latest_query_documents_message(db, conv_id)
        has_recent_evidence, evidence_hits = self._has_explicit_recent_evidence(
            user_message=user_message,
            chunks=latest_chunks,
        )
        high_risk_question = self._is_high_risk_question(user_message)
        query_shift = self._has_query_shift(user_message=user_message, chunks=latest_chunks)
        turns_since_last_retrieval = self._user_turns_since_message(
            db,
            conv_id,
            (latest_tool_msg.created_at if latest_tool_msg else None),
        )
        prefetch_top_k = 5
        should_retrieve = False
        prefetch_result: Optional[Dict[str, Any]] = None
        routing_mode = "no_decider"

        if decider:
            decision = decider.decide_documents(
                db=db,
                conversation_id=conv_id,
                user_message=user_message,
                lang=lang,
                principal=principal,
                doc_key=self.documents_doc_key,
            )
            route_trace["decider"] = decision
            available_count = int(decision.get("available_documents_count") or 0)
            if decision.get("needs_retrieval"):
                candidate_ids = list(decision.get("candidate_document_ids") or [])
                should_retrieve = True
                routing_mode = "decider_retrieval"
            elif high_risk_question and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_high_risk"
                forced_retrieval_reason = "high_risk_question"
            elif query_shift and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_query_shift"
                forced_retrieval_reason = "query_shift_detected"
            elif turns_since_last_retrieval > 1 and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_stale_reuse_window"
                forced_retrieval_reason = "reuse_window_exceeded"
            elif available_count > 0 and not has_recent_evidence:
                # Deterministic fallback:
                # if decider skips retrieval but we have docs and no explicit evidence
                # in recent retrieved chunks, force a new retrieval.
                should_retrieve = True
                routing_mode = "forced_fallback_no_evidence"
                forced_retrieval_reason = "no_explicit_recent_evidence"
            elif has_recent_evidence:
                routing_mode = "reuse_recent_evidence"
            else:
                routing_mode = "skip_no_catalog"

            route_trace["policy"] = {
                "has_recent_evidence": has_recent_evidence,
                "evidence_hits": evidence_hits,
                "high_risk_question": high_risk_question,
                "query_shift": query_shift,
                "turns_since_last_retrieval": turns_since_last_retrieval,
                "should_retrieve": should_retrieve,
                "fallback_forced": bool(not decision.get("needs_retrieval") and should_retrieve),
                "routing_mode": routing_mode,
                "forced_retrieval_reason": forced_retrieval_reason,
            }
            try:
                self.log.info(
                    "retrieval_routing_decision",
                    decider=decision,
                    has_recent_evidence=has_recent_evidence,
                    evidence_hits=evidence_hits,
                    high_risk_question=high_risk_question,
                    query_shift=query_shift,
                    turns_since_last_retrieval=turns_since_last_retrieval,
                    should_retrieve=should_retrieve,
                    fallback_forced=bool(not decision.get("needs_retrieval") and should_retrieve),
                    routing_mode=routing_mode,
                    forced_retrieval_reason=forced_retrieval_reason,
                )
            except Exception:
                pass

            if forced_retrieval_reason:
                try:
                    self.log.info(
                        "reuse_guard_trigger",
                        reason=forced_retrieval_reason,
                        routing_mode=routing_mode,
                        query=user_message,
                    )
                except Exception:
                    pass

            if should_retrieve and self.documents_tools:
                general_top_k = 10 if candidate_ids else 5
                prefetch_top_k = general_top_k
                general_result = self.documents_tools.query_documents(
                    query=user_message,
                    top_k=general_top_k,
                    document_uuids=None,
                    doc_key=self.documents_doc_key,
                    principal=principal,
                )
                focused_result: Optional[Dict[str, Any]] = None
                if candidate_ids:
                    focused_top_k = 10
                    prefetch_top_k = general_top_k + focused_top_k
                    focused_result = self.documents_tools.query_documents(
                        query=user_message,
                        top_k=focused_top_k,
                        document_uuids=candidate_ids,
                        doc_key=None,
                        principal=principal,
                    )
                prefetch_result = self._merge_prefetch_results(focused_result, general_result)
                route_trace["prefetch"] = {
                    "requested_query": user_message,
                    "prefetch_strategy": ("dual_general_plus_candidates" if candidate_ids else "single_general"),
                    "general_requested_top_k": general_top_k,
                    "focused_requested_top_k": (10 if candidate_ids else None),
                    "candidate_document_ids": candidate_ids,
                    "returned_chunks": len((prefetch_result or {}).get("chunks") or []),
                    "trace": (prefetch_result or {}).get("trace"),
                    "general_trace": (general_result or {}).get("trace"),
                    "focused_trace": (focused_result or {}).get("trace") if isinstance(focused_result, dict) else None,
                }
                try:
                    self.log.info(
                        "retrieval_prefetch_done",
                        requested_query=user_message,
                        prefetch_strategy=("dual_general_plus_candidates" if candidate_ids else "single_general"),
                        general_requested_top_k=general_top_k,
                        focused_requested_top_k=(10 if candidate_ids else None),
                        candidate_document_ids=candidate_ids,
                        returned_chunks=len((prefetch_result or {}).get("chunks") or []),
                        trace=(prefetch_result or {}).get("trace"),
                        general_trace=(general_result or {}).get("trace"),
                        focused_trace=(focused_result or {}).get("trace") if isinstance(focused_result, dict) else None,
                        routing_mode=routing_mode,
                    )
                except Exception:
                    pass

        effective_group_context = dict(group_context or {})
        if prefetch_result:
            chunks = (prefetch_result.get("chunks") or []) if isinstance(prefetch_result, dict) else []
            effective_group_context["retrieval_context"] = {
                "query": user_message,
                "chunks": [
                    {
                        "document_uuid": c.get("document_uuid"),
                        "name": c.get("name"),
                        "chunk_index": c.get("chunk_index"),
                        "score": c.get("score"),
                        # Keep full retrieved chunk text in group context.
                        # Indexed chunks are ~1000 chars, and truncating to 700 can drop
                        # decisive SOP lines that appear near the end of the chunk.
                        "text_chunk": str(c.get("text_chunk") or "")[:1400],
                    }
                    for c in chunks[:prefetch_top_k]
                    if isinstance(c, dict)
                ],
                "trace": (prefetch_result.get("trace") if isinstance(prefetch_result, dict) else None),
            }

        result = super().chat(
            db=db,
            channel=channel,
            user_message=user_message,
            external_conversation_id=external_conversation_id,
            principal=principal,
            lang=lang,
            candidate_document_ids=candidate_ids,
            document_route_trace=route_trace,
            agent_slug=agent_slug,
            group_id=group_id,
            group_context=effective_group_context or None,
            forced_document_ids=forced_document_ids,
        )
        return result
