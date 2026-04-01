from fpdf import FPDF

pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=16, style='B')

pdf.cell(0, 10, txt="CLÍNICA DE DIAGNÓSTICO POR IMAGEM", ln=1, align="C")
pdf.ln(5)

pdf.set_font("Arial", size=14, style='B')
pdf.cell(0, 10, txt="Relatório de Ressonância Magnética", ln=1, align="C")
pdf.ln(5)

pdf.set_font("Arial", size=12)
pdf.cell(0, 10, txt="Paciente: Erica Becker", ln=1, align="L")
pdf.cell(0, 10, txt="Data do Exame: 26/03/2026", ln=1, align="L")
pdf.cell(0, 10, txt="Exame: Ressonância Magnética do Encéfalo com Contraste", ln=1, align="L")

pdf.ln(10)

pdf.set_font("Arial", size=12, style='B')
pdf.cell(0, 10, txt="INDICAÇÃO CLÍNICA:", ln=1, align="L")
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 8, txt="Avaliação de cefaleia recorrente e tonturas.")

pdf.ln(5)

pdf.set_font("Arial", size=12, style='B')
pdf.cell(0, 10, txt="TÉCNICA DO EXAME:", ln=1, align="L")
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 8, txt="Exame realizado em equipamento de alto campo magnético. Foram obtidas sequências ponderadas em T1, T2, FLAIR, difusão e suscetibilidade magnética nos planos sagital, axial e coronal, antes e após a administração intravenosa de meio de contraste paramagnético (Gadolínio).")

pdf.ln(5)

pdf.set_font("Arial", size=12, style='B')
pdf.cell(0, 10, txt="RELATÓRIO:", ln=1, align="L")
pdf.set_font("Arial", size=12)
relatorio_texto = """- Sistema ventricular com morfologia, dimensões e topografia normais.
- Sulcos e fissuras cerebrais e cerebelares com conformação anatômica adequada para a faixa etária.
- Parênquima encefálico sem áreas focais de alteração de sinal. 
- Ausência de áreas com restrição à difusão da água, sugerindo não haver focos de infarto recente.
- Não foram observados sinais de hemorragia aguda, coleções expansivas ou desvios de estruturas da linha média.
- Após a injeção do contraste, não foram evidenciados focos de realce anômalo no parênquima cerebral, cerebelar ou tronco encefálico.
- Estruturas ósseas da calota craniana e esfenóide preservadas.
- Transição crânio-cervical sem anormalidades significativas."""
pdf.multi_cell(0, 8, txt=relatorio_texto)

pdf.ln(5)

pdf.set_font("Arial", size=12, style='B')
pdf.cell(0, 10, txt="IMPRESSÃO DIAGNÓSTICA:", ln=1, align="L")
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 8, txt="Ressonância magnética do encéfalo sem evidências de alterações morfológicas ou de sinal da atualidade. Estudo dentro dos limites da normalidade.")

pdf.ln(15)

pdf.set_font("Arial", size=12, style='I')
pdf.cell(0, 10, txt="Documento assinado eletronicamente por Dr. Especialista Radiologista - CRM 123456", ln=1, align="C")

pdf.output("relatorio_ressonancia_erica_becker.pdf")
