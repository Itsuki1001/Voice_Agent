from graph.graph_whatsapp import graph

from langchain_core.messages import AIMessage, RemoveMessage,SystemMessage,HumanMessage

def store_handoff_reply(
    thread_id: str,
    user_message: str,
    staff_text: str,
    graph_app=graph
):
    config = {"configurable": {"thread_id": thread_id}}
    current_state = graph.get_state(config)
    
    # Get the last message ID
    last_message = current_state.values["messages"][-1]
    
    # Remove the last message and add the new one
    graph.update_state(
        config, 
        {
            "messages": [
                RemoveMessage(id=last_message.id),  # Remove last AI message
                HumanMessage(content=f"Staff clarification for the Question '{user_message}': {staff_text}")        # Add your message
            ]
        }
    )