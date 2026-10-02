#!/usr/bin/env python3

import os

from google.adk.models.lite_llm import LiteLlm
from google.adk.agents import LlmAgent
from google.genai.types import (
    GenerateContentConfig,
    ToolConfig,
    FunctionCallingConfig,
    FunctionCallingConfigMode,
    )

from app.config.setting import get_settings
from app.schema.user_goal import SavingRecommendation

os.environ.setdefault(
    "ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS",
    str(get_settings().ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS),
)

class LLMDefinition:
    def __init__(self, instructions:str, prompts:str):
        self.instructions = instructions
        self.prompts = prompts

    def llm_definition(
            self,
            agent_name:str,
            tools:list = [],
            output_schema=None,
            function_calling_mode:FunctionCallingConfigMode=FunctionCallingConfigMode.ANY
            ) -> LlmAgent:
        """
        This function serves a wrapper to integrate llm with litellm
        """
        setting = get_settings()
        return LlmAgent(
            model=LiteLlm(model=setting.LLM_MODEL, api_key=setting.OPENROUTER_KEY),
            name=agent_name,
            instruction=self.instructions,
            tools=tools,
            output_schema=output_schema,
            generate_content_config=GenerateContentConfig(
                temperature=0.1,
                top_p=0.9,
                top_k=40,
                tool_config=ToolConfig(
                    function_calling_config=FunctionCallingConfig(
                        mode=function_calling_mode,
                    )
                ) if tools else None,
                )
                )

    async def recommendation_agent(self,
                                   agent_name="recommendation_agent"
                                   )->LlmAgent:
        """
        This builds a recommendation agent
        """
        return self.llm_definition(
            agent_name=agent_name,
            tools=[],
            output_schema=SavingRecommendation
        )
