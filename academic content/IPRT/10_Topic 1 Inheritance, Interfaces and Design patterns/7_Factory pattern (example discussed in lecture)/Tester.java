package empfactory;

public class Tester {

	public static void main(String[] args) {
		//Employee e=new Employee();
		EmployeeFactory ef=new EmployeeFactory();
		
		Employee f1=ef.getType('f', "20202020", "N Ndlovu", 120000);
		System.out.println(f1);
		Employee p1=ef.getType('p', "21212121", "T Ndlozi", 20);
		System.out.println(p1);		
	}

}
