from pydantic import BaseModel, Field


class SummaryAssessment(BaseModel):
    paper_summary: str = Field(
        description="""Provide a concise, structured summary of the paper focusing on its relevance to neural-symbolic AI. The summary should be 3-4 sentences long and cover these key aspects:
        1. CONTEXT: Briefly state the problem or challenge in neural-symbolic AI that the paper addresses.
        2. CONTRIBUTION: Clearly describe what the paper contributes to the field, being specific about any new methods, architectures, or insights presented.
        3. SIGNIFICANCE: Explain how this work advances neural-symbolic AI, mentioning any notable results or implications for future research.
            
        For surveys or review papers, focus on the scope of the review and its key findings or insights rather than specific technical contributions.
        The summary should be written in clear, academic language and avoid subjective evaluations. Focus on factual content and specific details rather than general statements. Do not simply restate the abstract, but synthesize the paper's key elements into a coherent narrative that emphasizes its relevance to neural-symbolic AI.
        
        Example format:
        "This paper addresses [specific challenge] in neural-symbolic AI by [main approach/contribution]. The authors [specific details about method/findings]. This work advances the field by [specific impact or implication]."
        """,
        default="Summary not available"
    )


class TakeawayAssessment(BaseModel):
    takeaways: str = Field(
        description="""Provide a concise (2-4 sentences) summary of the paper's most important takeaways, focusing on actionable insights and significant contributions to neural-symbolic AI. The summary should:
        1. Identify the paper's main technical or theoretical contribution(s) to neural-symbolic integration, being specific about:
           - Novel methods, architectures, or theoretical frameworks introduced
           - Key performance improvements or capabilities demonstrated
           - Important limitations or challenges identified
        2. Highlight practical implications such as:
           - How the work advances the state of neural-symbolic integration
           - What specific problems or limitations it addresses
           - Which aspects of prior approaches it improves upon
        3. Note any significant empirical findings:
           - Quantitative results that demonstrate advantages over existing approaches
           - Important experimental insights or unexpected discoveries
           - Practical constraints or requirements identified
        
        Focus on extracting insights that would be valuable for understanding the evolution and current state of neural-symbolic AI. Avoid general descriptions or background information, and instead emphasize specific, concrete findings or advances. If the paper presents multiple contributions, prioritize the most significant or novel ones.
        """,
        default="Takeaways not available"
    )
