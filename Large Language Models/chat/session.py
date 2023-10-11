import os
import guidance
from dotenv import load_dotenv


# Class to manage a conversation
class Session:
    def __init__(self,
                 llm_model: str = "gpt-3.5-turbo",
                 assistant_description: str = "You are a helpful assistant.",
                 history: list[str] = None):
        load_dotenv()
        self.llm_model = guidance.llms.OpenAI(
            llm_model, api_key=os.getenv("OPENAI_API_KEY"))
        self.llm_role = assistant_description
        self.template = "{{#system~}}{{system_prompt}}{{~/system}}"
        if history is not None:
            for i in range(0, len(history), 2):
                self.template += "{{#user~}}{}{{~/user}}".format(history[i])
                if i + 1 < len(history):
                    self.template += "{{#assistant~}}{}{{~/assistant}}".format(history[i + 1])

    # Method to generate a response
    def generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 256) -> str:
        # Create the template
        generate_template = self.template + """{{#user~}}{{prompt}}{{~/user}}{{#assistant~}}
        {{gen 'generated_answer' temperature={} max_tokens={}}}
        {{~/assistant}}""".format(str(temperature), str(max_tokens))

        # Create the guidance program using the template and model
        generate_program = guidance.Program(generate_template, llm=self.llm_model)

        try:
            # Generate the answer
            generate_answer = generate_program(
                system_prompt=self.llm_role, prompt=prompt).variables().get('generated_answer')

            # Add the messages to the template
            self.add_message_user(prompt)
            self.add_message_assistant(generate_answer)

            # Return the answer
            return generate_answer
        except Exception as e:
            # If there is an error, return the error
            return f"ERROR: {e}"

    # Method to add a message from the user to the template
    def add_message_user(self, message: str):
        self.template += f"{{#user~}}{message}{{~/user}}"

    # Method to add a message from the assistant to the template
    def add_message_assistant(self, message: str):
        self.template += f"{{#assistant~}}{message}{{~/assistant}}"
