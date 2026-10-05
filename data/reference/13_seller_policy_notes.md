# Phase 3 seller proceeds: source and rule notes

Checked 29 September 2026. This is a local planning model for a **whole-flat sale**. It cannot replace HDB's sale proceeds statement, CPF's dashboard, or a lender's redemption statement.

| Item | Implemented treatment | Official source |
|---|---|---|
| Sale proceeds | The sale price is allocated to the outstanding housing loan, then CPF refund, then entered sale costs; the remainder is estimated cash proceeds. Option money already received is part of the sale price, so do not add it again. The planner does not model payment dates. | [HDB Intent to Sell and sale proceeds](https://www.hdb.gov.sg/managing-my-home/selling-a-flat/process-for-selling-a-flat/intent-to-sell?anchor=sales-proceeds), [CPF refund order](https://www.cpf.gov.sg/service/article/i-have-made-a-voluntary-refund-do-i-still-need-to-refund-to-my-cpf-account-when-i-sell-my-property) |
| Housing loan | Enter the balance expected at completion from MyHDB or the lender, including any applicable redemption charges. When price cannot settle the loan, the tool shows a cash top-up risk. | [HDB Intent to Sell](https://www.hdb.gov.sg/managing-my-home/selling-a-flat/process-for-selling-a-flat/intent-to-sell?anchor=sales-proceeds) |
| CPF refund | Prefer the total required refund for **all owners** from CPF's Home ownership dashboard. Alternatively enter principal used, accrued interest, and any other required refund. The tool does not calculate accrued interest from dates. | [CPF refund when selling](https://www.cpf.gov.sg/member/home-ownership/using-your-cpf-to-buy-a-home/cpf-refund-when-selling-or-transferring-property) |
| Insufficient CPF refund | The tool displays the amount not covered by proceeds. CPF says a cash top-up is generally not required when sold at market value, but below-market sales can require one. The tool does not decide market value or legal treatment. | [CPF shortfall guidance](https://www.cpf.gov.sg/service/article/how-much-do-i-need-to-refund-to-my-cpf-account-if-i-am-selling-my-whole-property) |
| CPF reuse for another property | CPF returned to an account is distinct from sale cash. At age 55 or above, some returned amounts may go to the Retirement Account. A second HDB loan can also depend on the CPF refund and part of cash proceeds. | [CPF refund and account treatment](https://www.cpf.gov.sg/member/home-ownership/using-your-cpf-to-buy-a-home/cpf-refund-when-selling-or-transferring-property), [HDB resale financing](https://www.hdb.gov.sg/sitecore/content/hdbinfoweb/home/buying-a-flat/resale-flats/process-for-buying-a-resale-flat/resale-flat-planning/mode-of-financing) |
| Fees and levy | Agent, legal, resale levy, upgrading and other costs are entered by the seller in dollars. The calculator does not infer which charges apply or when they are payable. Verify a resale levy and its settlement treatment with HDB before entering it as a sale deduction. | [HDB Intent to Sell](https://www.hdb.gov.sg/managing-my-home/selling-a-flat/process-for-selling-a-flat/intent-to-sell?anchor=sales-proceeds) |

## Planning assumptions and limits

- If low or high sale prices are blank, the tool uses **5% below and 5% above** the expected price as illustrations. They are not model forecasts.
- The required CPF refund and outstanding loan are held fixed across the three price scenarios. Update them when the expected completion date changes.
- Cash proceeds are not the same as profit. The calculation does not include the original purchase price, past interest payments, maintenance, property tax, or opportunity cost.
- The sale price is allocated in the order loan, CPF, then entered costs. Actual HDB settlement lines and timing may differ. If costs cannot be covered from proceeds, the tool flags a cash requirement.
- The optional next-home comparison places sale cash against buyer cash needs and CPF refund against buyer CPF needs. It does not add other savings or establish how much CPF can be reused. A buyer plan may need to be recalculated after the sale details are known.
- A transfer of only one owner's share, a below-market sale, special age-55 rules, and disputed or unusual CPF allocations need individual review. This planner models a whole-flat sale only.

