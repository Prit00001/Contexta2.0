# Evaluation Dataset Review Sheet

These labels were drafted by an LLM (Claude) directly against the real 25-paper corpus in `data/documents.py`, not independently sourced - unreviewed items may therefore inflate measured accuracy, since the same model that wrote the question also knows the right answer. Every row below has `reviewed: false` in `questions.jsonl`. Please scan each row and flag anything wrong: a `gold_document_ids` that doesn't actually support the question, a question that's ambiguous or double-barreled, an `unanswerable` question that's actually answerable from the corpus (or vice versa), or wording that leaks the answer. Once you're satisfied with a row, flip its `reviewed` field to `true` in the JSONL (the eval harness reports reviewed vs. unreviewed metrics separately).

| id | type | question | gold_document_ids | notes to check |
|---|---|---|---|---|
| q001 | single_fact | What does the Transformer use instead of recurrence and convolution? | doc_001 | |
| q002 | single_fact | What two objectives does BERT use for pre-training? | doc_002 | |
| q003 | single_fact | How many parameters does GPT-3 have? | doc_003 | |
| q004 | single_fact | What two components does retrieval-augmented generation combine? | doc_004 | |
| q005 | single_fact | How many BERT encoders does Dense Passage Retrieval train? | doc_005 | |
| q006 | single_fact | What architecture does Sentence-BERT use to produce sentence embeddings? | doc_006 | |
| q007 | single_fact | What kind of index does FAISS offer for exact similarity search? | doc_007 | |
| q008 | single_fact | What structure does HNSW build for approximate nearest neighbor search? | doc_008 | |
| q009 | single_fact | What does LoRA freeze during fine-tuning? | doc_009 | |
| q010 | single_fact | What quantization format does QLoRA use for the frozen base model? | doc_010 | |
| q011 | single_fact | What format does T5 cast every NLP task into? | doc_011 | |
| q012 | single_fact | Name one training change RoBERTa makes relative to BERT. | doc_012 | |
| q013 | single_fact | What technique does DistilBERT use to shrink BERT? | doc_013 | |
| q014 | single_fact | What does chain-of-thought prompting add to few-shot examples? | doc_014 | |
| q015 | single_fact | What does ReAct interleave with reasoning traces? | doc_015 | |
| q016 | single_fact | What two training stages does InstructGPT apply to GPT-3? | doc_016 | |
| q017 | single_fact | How does language model loss relate to model size, dataset size and compute, per the scaling laws paper? | doc_017 | |
| q018 | single_fact | According to Chinchilla, what should scale roughly equally during training? | doc_018 | |
| q019 | single_fact | What problem does FlashAttention avoid by tiling computation into on-chip SRAM? | doc_019 | |
| q020 | single_fact | What is LLaMA trained only on? | doc_020 | |
| q021 | single_fact | What two training objectives does Word2Vec use? | doc_021 | |
| q022 | single_fact | What does ELMo derive word vectors from? | doc_022 | |
| q023 | single_fact | What operation does ColBERT use to score relevance between query and document token vectors? | doc_023 | |
| q024 | single_fact | What special tokens does Self-RAG use to decide when to retrieve and to critique its own output? | doc_024 | |
| q025 | single_fact | What does Switch Transformers route each token to? | doc_025 | |
| q026 | single_fact | What benefit does RAG claim for reducing hallucination? | doc_004 | |
| q027 | single_fact | How much does QLoRA claim you can fine-tune on a single 48 GB GPU? | doc_010 | |
| q028 | single_fact | What was found to be true of many large language models, according to the Chinchilla paper? | doc_018 | |
| q029 | single_fact | What does InstructGPT's reward model get trained on? | doc_016 | |
| q030 | single_fact | At what point do the benefits of chain-of-thought prompting mainly emerge? | doc_014 | |
| q031 | single_fact | What percentage of BERT's language understanding performance does DistilBERT retain? | doc_013 | |
| q032 | single_fact | What corpus does the T5 paper introduce? | doc_011 | |
| q033 | single_fact | What does LLaMA-13B outperform on most benchmarks, according to the paper? | doc_020 | |
| q034 | single_fact | What kind of word-vector relationship does Word2Vec's vector arithmetic capture? | doc_021 | |
| q035 | single_fact | What is FAISS primarily designed to search over, at what scale? | doc_007 | |
| q036 | single_fact | What is HNSW commonly used as, in vector databases? | doc_008 | |
| q037 | single_fact | What speedup/memory claim does the Switch Transformers paper make about compute per token? | doc_025 | |
| q038 | single_fact | What does Self-RAG improve compared to always retrieving a fixed number of passages? | doc_024 | |
| q039 | single_fact | What environments does ReAct improve success in? | doc_015 | |
| q040 | paraphrase | How does the Transformer model handle word order without using recurrence? | doc_001 | |
| q041 | paraphrase | Why can one pre-trained BERT model be adapted to many different NLP tasks with just a small extra layer? | doc_002 | |
| q042 | paraphrase | How does GPT-3 learn to do a new task without updating its weights? | doc_003 | |
| q043 | paraphrase | How can a language model answer questions using outside documents instead of only what it memorized? | doc_004 | |
| q044 | paraphrase | Why does encoding questions and passages separately help search go faster than old-style keyword matching? | doc_005 | |
| q045 | paraphrase | What makes it possible to compare the meaning of two sentences with a simple cosine similarity score? | doc_006 | |
| q046 | paraphrase | What library would you reach for to do nearest-neighbor search across a billion embeddings efficiently? | doc_007 | |
| q047 | paraphrase | How do some vector databases find approximate nearest neighbors quickly without scanning everything? | doc_008 | |
| q048 | paraphrase | How can you make fine-tuning a huge language model cheaper without touching most of its weights? | doc_009 | |
| q049 | paraphrase | How can you fine-tune a very large model on a single consumer-ish GPU? | doc_010 | |
| q050 | paraphrase | What's the idea behind treating every language task - translation, summarization, classification - the same way? | doc_011 | |
| q051 | paraphrase | Was BERT trained as well as it could have been? | doc_012 | |
| q052 | paraphrase | How do you get a smaller, faster version of a big language model without losing much accuracy? | doc_013 | |
| q053 | paraphrase | Why does asking a model to 'think step by step' improve its answers on math problems? | doc_014 | |
| q054 | paraphrase | How can a language model use a search engine or other tool while it reasons through a problem? | doc_015 | |
| q055 | paraphrase | How do you make a language model better at following what people actually want, not just predicting text? | doc_016 | |
| q056 | paraphrase | Does making a language model bigger always help in the same way? | doc_017 | |
| q057 | paraphrase | Were the biggest language models trained with the right balance of size versus data? | doc_018 | |
| q058 | paraphrase | How can attention be computed exactly without running out of GPU memory on long sequences? | doc_019 | |
| q059 | paraphrase | What's notable about the data used to train LLaMA? | doc_020 | |
| q060 | paraphrase | How do you turn words into vectors so that 'king' minus 'man' plus 'woman' lands near 'queen'? | doc_021 | |
| q061 | paraphrase | How can the same word get a different vector depending on the sentence it's in? | doc_022 | |
| q062 | paraphrase | How do you get most of the accuracy of comparing a query against a document token-by-token, without the full cost? | doc_023 | |
| q063 | paraphrase | How can a model decide for itself when it needs to look something up versus just answering? | doc_024 | |
| q064 | paraphrase | How do you scale a model to a trillion parameters without a proportional increase in compute per input? | doc_025 | |
| q065 | paraphrase | Can a RAG system's knowledge be updated without retraining the whole model? | doc_004 | |
| q066 | paraphrase | What's the largest model QLoRA claims you can fine-tune on one GPU, and how big is that GPU? | doc_010 | |
| q067 | paraphrase | Were most big language models undertrained or overtrained, according to Chinchilla? | doc_018 | |
| q068 | paraphrase | What kind of data trains InstructGPT's reward model? | doc_016 | |
| q069 | paraphrase | At what point does step-by-step prompting actually start helping? | doc_014 | |
| q070 | paraphrase | Roughly how much of BERT's accuracy does the distilled, smaller version keep? | doc_013 | |
| q071 | paraphrase | What new dataset came out of the T5 work? | doc_011 | |
| q072 | paraphrase | Can a 13-billion-parameter open model beat GPT-3 while running on a single GPU? | doc_020 | |
| q073 | paraphrase | What's an example of the kind of analogy word embeddings can solve? | doc_021 | |
| q074 | paraphrase | What scale of data was FAISS designed to handle? | doc_007 | |
| q075 | paraphrase | Is HNSW mostly an exact or approximate search method? | doc_008 | |
| q076 | paraphrase | Does adding more experts to a Switch Transformer increase the compute cost per token? | doc_025 | |
| q077 | paraphrase | Does Self-RAG retrieve the same fixed number of passages every time? | doc_024 | |
| q078 | paraphrase | Besides reducing hallucination in QA, what else does ReAct help with? | doc_015 | |
| q079 | comparison | How does RoBERTa differ from the original BERT training recipe? | doc_002, doc_012 | |
| q080 | comparison | How does DistilBERT's approach to efficiency differ from RoBERTa's approach to accuracy? | doc_013, doc_012 | |
| q081 | comparison | Compare how BERT and DistilBERT relate to each other. | doc_002, doc_013 | |
| q082 | comparison | What's the key difference between LoRA and QLoRA? | doc_009, doc_010 | |
| q083 | comparison | How do DPR and ColBERT differ in how they represent passages for retrieval? | doc_005, doc_023 | |
| q084 | comparison | How does Dense Passage Retrieval differ from Sentence-BERT in what they're optimized for? | doc_005, doc_006 | |
| q085 | comparison | Compare ColBERT and Sentence-BERT's representations of text. | doc_023, doc_006 | |
| q086 | comparison | What's the main tradeoff between FAISS's flat index and HNSW? | doc_007, doc_008 | |
| q087 | comparison | How does InstructGPT build on GPT-3? | doc_003, doc_016 | |
| q088 | comparison | How does LLaMA's approach to scale differ from GPT-3's? | doc_003, doc_020 | |
| q089 | comparison | How do the scaling-laws paper and Chinchilla disagree about compute-optimal training? | doc_017, doc_018 | |
| q090 | comparison | How does Chinchilla's finding relate to how LLaMA was trained? | doc_018, doc_020 | |
| q091 | comparison | Compare Word2Vec and ELMo's word representations. | doc_021, doc_022 | |
| q092 | comparison | How do chain-of-thought prompting and ReAct differ in what they add to a model's output? | doc_014, doc_015 | |
| q093 | comparison | How does Self-RAG build on the original RAG idea? | doc_004, doc_024 | |
| q094 | comparison | How does RAG's retrieval approach compare to DPR's? | doc_004, doc_005 | |
| q095 | comparison | How does Self-RAG's adaptive retrieval compare to ReAct's action loop? | doc_024, doc_015 | |
| q096 | comparison | What problem does FlashAttention solve that the original Transformer architecture doesn't address? | doc_001, doc_019 | |
| q097 | comparison | How does Switch Transformers scale differently than a standard Transformer? | doc_001, doc_025 | |
| q098 | comparison | Compare how LoRA and Switch Transformers each aim for parameter efficiency. | doc_009, doc_025 | |
| q099 | comparison | How does T5's text-to-text framing differ from BERT's approach to NLP tasks? | doc_011, doc_002 | |
| q100 | comparison | How does T5 differ from GPT-3 in how it's trained to do many tasks? | doc_011, doc_003 | |
| q101 | comparison | Compare QLoRA and FlashAttention as memory-efficiency techniques. | doc_010, doc_019 | |
| q102 | comparison | Compare DistilBERT and QLoRA as approaches to making models cheaper to use. | doc_013, doc_010 | |
| q103 | comparison | How do InstructGPT and ReAct differ in what kind of model behavior they target? | doc_016, doc_015 | |
| q104 | comparison | How does the Chinchilla paper's conclusion relate to Switch Transformers' scaling approach? | doc_018, doc_025 | |
| q105 | comparison | Compare HNSW and ColBERT as approaches to efficient search. | doc_008, doc_023 | |
| q106 | comparison | Compare Sentence-BERT and ELMo's approaches to representing meaning. | doc_006, doc_022 | |
| q107 | comparison | How does chain-of-thought prompting relate to how InstructGPT is trained? | doc_014, doc_016 | |
| q108 | comparison | How do LLaMA, Chinchilla and the original scaling-laws paper relate to each other on compute-optimal training? | doc_020, doc_018, doc_017 | |
| q109 | comparison | How does BERT's approach to context differ from ELMo's? | doc_002, doc_022 | |
| q110 | comparison | Compare RAG, Self-RAG and ReAct as ways of grounding or acting on external information. | doc_004, doc_024, doc_015 | |
| q111 | unanswerable | How do diffusion models generate images from noise? | (none - unanswerable) | |
| q112 | unanswerable | What is the architecture of a Generative Adversarial Network (GAN) and how is it trained? | (none - unanswerable) | |
| q113 | unanswerable | How does CLIP learn joint representations of images and text? | (none - unanswerable) | |
| q114 | unanswerable | What are the key hyperparameters of the PPO (Proximal Policy Optimization) algorithm? | (none - unanswerable) | |
| q115 | unanswerable | How does byte-pair encoding (BPE) build a subword vocabulary? | (none - unanswerable) | |
| q116 | unanswerable | What architectural innovations does ResNet introduce for training very deep convolutional networks? | (none - unanswerable) | |
| q117 | unanswerable | How do graph neural networks propagate information across graph-structured data? | (none - unanswerable) | |
| q118 | unanswerable | How does federated learning train a model across devices without centralizing data? | (none - unanswerable) | |
| q119 | unanswerable | How is differential privacy enforced during neural network training? | (none - unanswerable) | |
| q120 | unanswerable | How does speculative decoding speed up autoregressive LLM inference? | (none - unanswerable) | |
| q121 | unanswerable | How does GPTQ perform post-training quantization of a language model? | (none - unanswerable) | |
| q122 | unanswerable | What indexing strategies does the Qdrant vector database use internally? | (none - unanswerable) | |
| q123 | unanswerable | What is Constitutional AI and how does it shape model behavior without human feedback? | (none - unanswerable) | |
| q124 | unanswerable | How does Direct Preference Optimization (DPO) differ from standard RLHF in its training objective? | (none - unanswerable) | |
| q125 | unanswerable | How does the Mixtral mixture-of-experts model select which experts to activate? | (none - unanswerable) | |
| q126 | unanswerable | How does the Whisper model perform automatic speech recognition across languages? | (none - unanswerable) | |
| q127 | unanswerable | What is reinforcement learning from AI feedback (RLAIF) and how does it reduce the need for human labels? | (none - unanswerable) | |
| q128 | unanswerable | How does model merging (e.g. weight averaging across fine-tunes) combine multiple models into one? | (none - unanswerable) | |
| q129 | unanswerable | What techniques mitigate catastrophic forgetting when a model is fine-tuned on a new task? | (none - unanswerable) | |
| q130 | unanswerable | How do continual learning methods let a model learn new tasks without retraining from scratch? | (none - unanswerable) | |
| q131 | unanswerable | What kinds of adversarial prompts can jailbreak an aligned language model? | (none - unanswerable) | |
| q132 | unanswerable | How does watermarking of LLM-generated text work? | (none - unanswerable) | |
| q133 | unanswerable | How can you detect whether a benchmark's test set leaked into an LLM's training data? | (none - unanswerable) | |
| q134 | unanswerable | How is synthetic data generated and filtered for training large language models? | (none - unanswerable) | |
| q135 | unanswerable | How does a cross-encoder re-ranker differ from a bi-encoder in a search pipeline? | (none - unanswerable) | |
| q136 | unanswerable | How do multi-agent LLM frameworks coordinate several agents on a shared task? | (none - unanswerable) | |
| q137 | unanswerable | How does RoPE (rotary position embedding) scaling extend a model's context length? | (none - unanswerable) | |
| q138 | unanswerable | How do state space models like Mamba process long sequences compared to attention? | (none - unanswerable) | |
| q139 | unanswerable | How does a byte-level, tokenizer-free model like ByT5 handle text without a subword vocabulary? | (none - unanswerable) | |
| q140 | unanswerable | What techniques compress the KV cache to reduce memory use during LLM inference? | (none - unanswerable) | |
| q141 | unanswerable | What defenses exist against prompt injection attacks on LLM applications? | (none - unanswerable) | |
| q142 | unanswerable | How does a vision transformer differ from a convolutional network for image classification? | (none - unanswerable) | |
| q143 | unanswerable | How does AlphaFold predict protein structure from amino acid sequences? | (none - unanswerable) | |
