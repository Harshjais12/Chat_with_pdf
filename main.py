import streamlit as st
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_community.vectorstores import FAISS  # Replaced Chroma with FAISS
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
import os
import tempfile
import shutil

from dotenv import load_dotenv
load_dotenv()

# Initialize embeddings
@st.cache_resource
def get_embeddings():
    """Initialize embeddings with caching"""
    os.environ['HF_TOKEN'] = os.getenv("HF_TOKEN", "")
    return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

embeddings = get_embeddings()

# Page configuration
st.set_page_config(
    page_title="Conversational RAG with PDFs",
    page_icon="📚",
    layout="wide"
)

st.title("📚 Conversational RAG With PDF uploads and chat history")
st.markdown("Upload PDFs and chat with their content while maintaining conversation history!")

# API Key input
api_key = st.text_input("Enter your Groq API key:", type="password")

if api_key:
    try:
        # Initialize LLM
        llm = ChatGroq(groq_api_key=api_key, model_name="gemma2-9b-it")
        
        # Session management
        session_id = st.text_input("Session ID", value="default_session", help="Use different session IDs to maintain separate conversations")
        
        # Initialize session state
        if 'store' not in st.session_state:
            st.session_state.store = {}
        if 'vectorstore' not in st.session_state:
            st.session_state.vectorstore = None
        if 'documents_processed' not in st.session_state:
            st.session_state.documents_processed = False
        
        # File upload section
        st.markdown("### 📁 Upload Documents")
        uploaded_files = st.file_uploader(
            "Choose PDF files", 
            type="pdf", 
            accept_multiple_files=True,
            help="Upload one or more PDF files to chat with"
        )
        
        # Process documents
        if uploaded_files and not st.session_state.documents_processed:
            if st.button("🔄 Process Documents", type="primary"):
                with st.spinner("Processing documents... This may take a moment."):
                    try:
                        documents = []
                        
                        # Create temporary directory
                        temp_dir = tempfile.mkdtemp()
                        
                        for uploaded_file in uploaded_files:
                            # Create temporary file
                            temp_pdf_path = os.path.join(temp_dir, f"temp_{uploaded_file.name}")
                            with open(temp_pdf_path, "wb") as file:
                                file.write(uploaded_file.getvalue())
                            
                            # Load PDF
                            loader = PyPDFLoader(temp_pdf_path)
                            docs = loader.load()
                            documents.extend(docs)
                        
                        if documents:
                            # Split documents
                            text_splitter = RecursiveCharacterTextSplitter(
                                chunk_size=1000, 
                                chunk_overlap=200
                            )
                            splits = text_splitter.split_documents(documents)
                            
                            # Create FAISS vector store (instead of Chroma)
                            vectorstore = FAISS.from_documents(documents=splits, embedding=embeddings)
                            st.session_state.vectorstore = vectorstore
                            st.session_state.documents_processed = True
                            
                            st.success(f"✅ Successfully processed {len(uploaded_files)} PDF file(s) into {len(splits)} chunks!")
                        else:
                            st.error("❌ No content could be extracted from the uploaded PDFs.")
                        
                        # Clean up temporary directory
                        shutil.rmtree(temp_dir)
                        
                    except Exception as e:
                        st.error(f"❌ Error processing documents: {str(e)}")
                        # Clean up on error
                        if 'temp_dir' in locals():
                            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Chat interface (only show if documents are processed)
        if st.session_state.documents_processed and st.session_state.vectorstore:
            retriever = st.session_state.vectorstore.as_retriever(
                search_kwargs={"k": 3}  # Retrieve top 3 most relevant chunks
            )
            
            # Contextualization prompt
            contextualize_q_system_prompt = (
                "Given a chat history and the latest user question "
                "which might reference context in the chat history, "
                "formulate a standalone question which can be understood "
                "without the chat history. Do NOT answer the question, "
                "just reformulate it if needed and otherwise return it as is."
            )
            
            contextualize_q_prompt = ChatPromptTemplate.from_messages([
                ("system", contextualize_q_system_prompt),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ])
            
            # Create history-aware retriever
            history_aware_retriever = create_history_aware_retriever(
                llm, retriever, contextualize_q_prompt
            )
            
            # QA prompt
            system_prompt = (
                "You are an assistant for question-answering tasks. "
                "Use the following pieces of retrieved context to answer "
                "the question. If you don't know the answer, say that you "
                "don't know. Use three sentences maximum and keep the "
                "answer concise."
                "\n\n"
                "{context}"
            )
            
            qa_prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ])
            
            # Create chains
            question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)
            rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)
            
            # Session history function
            def get_session_history(session: str) -> BaseChatMessageHistory:
                if session not in st.session_state.store:
                    st.session_state.store[session] = ChatMessageHistory()
                return st.session_state.store[session]
            
            # Create conversational RAG chain
            conversational_rag_chain = RunnableWithMessageHistory(
                rag_chain,
                get_session_history,
                input_messages_key="input",
                history_messages_key="chat_history",
                output_messages_key="answer"
            )
            
            # Chat interface
            st.markdown("### 💬 Chat with your Documents")
            
            # Display current session info
            col1, col2 = st.columns([2, 1])
            with col1:
                user_input = st.text_input(
                    "Your question:", 
                    placeholder="Ask something about your documents...",
                    key="user_question"
                )
            with col2:
                if st.button("🗑️ Clear Chat History"):
                    if session_id in st.session_state.store:
                        st.session_state.store[session_id] = ChatMessageHistory()
                    st.success("Chat history cleared!")
                    st.rerun()
            
            # Process user input
            if user_input:
                try:
                    with st.spinner("Thinking..."):
                        session_history = get_session_history(session_id)
                        response = conversational_rag_chain.invoke(
                            {"input": user_input},
                            config={"configurable": {"session_id": session_id}},
                        )
                    
                    # Display response
                    st.markdown("**Assistant:**")
                    st.write(response['answer'])
                    
                    # Show source documents in expander
                    if 'context' in response and response['context']:
                        with st.expander(f"📄 Source Documents ({len(response['context'])} chunks)", expanded=False):
                            for i, doc in enumerate(response['context']):
                                st.markdown(f"**Chunk {i+1}:**")
                                st.text(doc.page_content[:300] + "..." if len(doc.page_content) > 300 else doc.page_content)
                                st.markdown("---")
                    
                except Exception as e:
                    st.error(f"❌ Error generating response: {str(e)}")
            
            # Display chat history
            if session_id in st.session_state.store:
                session_history = st.session_state.store[session_id]
                if session_history.messages:
                    st.markdown("### 📋 Chat History")
                    with st.expander("View conversation history", expanded=False):
                        for i, message in enumerate(session_history.messages):
                            if message.type == "human":
                                st.markdown(f"**You:** {message.content}")
                            elif message.type == "ai":
                                st.markdown(f"**Assistant:** {message.content}")
                            if i < len(session_history.messages) - 1:
                                st.markdown("---")
        
        elif uploaded_files:
            st.info("👆 Please click 'Process Documents' to analyze your PDFs before chatting.")
        else:
            st.info("📁 Please upload PDF files to get started.")
            
    except Exception as e:
        st.error(f"❌ Error initializing the application: {str(e)}")
        st.info("Please check your Groq API key and try again.")

else:
    st.warning("🔑 Please enter your Groq API key to get started.")
    st.info("You can get your Groq API key from: https://console.groq.com/")

# Sidebar with instructions
with st.sidebar:
    st.markdown("### 📖 Instructions")
    st.markdown("1. Enter your Groq API key")
    st.markdown("2. Upload one or more PDF files")
    st.markdown("3. Click 'Process Documents'")
    st.markdown("4. Start asking questions!")
    
    st.markdown("### ⚙️ Features")
    st.markdown("✅ Multiple PDF support")
    st.markdown("✅ Conversation memory")
    st.markdown("✅ Source document references")
    st.markdown("✅ Session management")
    
    st.markdown("### 🔧 Technical Details")
    st.markdown("- **Vector Store:** FAISS")
    st.markdown("- **Embeddings:** HuggingFace MiniLM")
    st.markdown("- **LLM:** Groq Gemma2-9b-it")
    st.markdown("- **Chunking:** 1000 chars with 200 overlap")        
